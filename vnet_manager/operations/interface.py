import shlex
from pyroute2 import IPRoute, NDB
from logging import getLogger
from subprocess import check_call, CalledProcessError, Popen, DEVNULL
from os.path import join
from psutil import process_iter
from tabulate import tabulate

from vnet_manager.conf import settings
from vnet_manager.utils.mac import random_mac_generator

logger = getLogger(__name__)


def get_vnet_interface_names_from_config(config):
    """
    Gets the VNet inetface names from the config
    :param dict config: The conifg generated by get_config()
    :return: list: The VNet interface names
    """
    return [settings.VNET_BRIDGE_NAME + str(i) for i in range(0, config["switches"])]


def get_machines_by_vnet_interface_name(config, ifname):
    """
    Returns a list of machine that use a particular VNet interface
    :param dict config: The config generated by get_config()
    :param str ifname: The interface to check for
    :return: list of VNet machines using that interface
    """
    machines = []
    for m_name, m_data in config["machines"].items():
        for int_data in m_data["interfaces"].values():
            if int(int_data["bridge"]) == int(ifname[-1]):
                machines.append(m_name)
    return machines


def show_vnet_interface_status(config):
    """
    Shows the VNet interface status to the user
    :param dict config: The config generated by get_config()
    """
    logger.info("Listing VNet interface statuses")
    header = ["Name", "Status", "L2_addr", "Sniffer", "STP", "Used by"]
    statuses = []
    ip = IPRoute()
    ndb = NDB(log=False)
    for ifname in get_vnet_interface_names_from_config(config):
        used_by = get_machines_by_vnet_interface_name(config, ifname)
        dev = ip.link_lookup(ifname=ifname)
        if not dev:
            # Link does not exist
            statuses.append([ifname, "NA", "NA", "NA", "NA", ", ".join(used_by)])
        else:
            # Get the link info
            sniffer = check_if_sniffer_exists(ifname)
            with ndb.interfaces[ifname] as info:
                statuses.append([ifname, info["state"], info["address"], sniffer, bool(info["br_stp_state"]), ", ".join(used_by)])
    print(tabulate(statuses, headers=header, tablefmt="pretty"))


def show_vnet_veth_interface_status(config):
    """
    Shows the VNet veth interface status to the user
    Assumes that the 'veths' config is present
    :param dict config: The config generated by get_config
    """
    logger.info("Listing VNet veth interface statuses")
    header = ["Name", "Status", "L2_addr", "Peer", "Master"]
    statuses = []
    ip = IPRoute()
    for name, data in config["veths"].items():
        dev = ip.link_lookup(ifname=name)
        if not dev:
            # Link does not exist
            statuses.append([name, "NA", "NA", "NA", data["bridge"]])
        else:
            # Get the link info
            info = ip.link("get", index=dev[0])
            state = info[0]["state"]
            l2_addr = [attr[1] for attr in info[0]["attrs"] if attr[0] == "IFLA_ADDRESS"][0]
            peer_id = [attr[1] for attr in info[0]["attrs"] if attr[0] == "IFLA_LINK"][0]
            peer_name = [attr[1] for attr in ip.link("get", index=peer_id)[0]["attrs"] if attr[0] == "IFLA_IFNAME"][0]
            master_id = [attr[1] for attr in info[0]["attrs"] if attr[0] == "IFLA_MASTER"][0]
            master_name = [attr[1] for attr in ip.link("get", index=master_id)[0]["attrs"] if attr[0] == "IFLA_IFNAME"][0]
            statuses.append([name, state, l2_addr, peer_name, master_name])
    print(tabulate(statuses, headers=header, tablefmt="pretty"))


def check_if_interface_exists(ifname):
    """
    Check if an interface exists
    :param str ifname: The interface name to check for
    :return: bool: True if the interface exists, False otherwise
    """
    return bool(IPRoute().link_lookup(ifname=ifname))


def create_vnet_interface(ifname):
    """
    Creates a VNet bridge interface
    :param str ifname: The name of the interface to create
    """
    logger.info("Creating VNet bridge interface {}".format(ifname))
    IPRoute().link("add", ifname=ifname, kind="bridge")
    # Bring up the interface
    configure_vnet_interface(ifname)


def create_veth_interface(name, data):
    """
    Creates a veth interface pair
    :param str name: The name of the veth interface to create
    :param dict data: The bridge and peer data
    """
    # We only create the interface if it has a peer
    if "peer" in data:
        IPRoute().link("add", ifname=name, kind="veth", peer=data["peer"])


def create_vnet_interface_iptables_rules(ifname):
    """
    VNet interfaces should act as dump bridges and should not have any connectivity to the outside world
    So this function makes some IPtables rules to make sure the VNet interface cannot talk to the outside.
    :param str ifname: The interface the create IPtables rules for
    """
    rule = "OUTPUT -o {} -j DROP".format(ifname)
    # First we check if the rule already exists
    try:
        check_call(shlex.split("iptables -C {}".format(rule)), stderr=DEVNULL)
        logger.debug("IPtables DROP rule for VNet interface {} already exists, skipping creation".format(ifname))
    except CalledProcessError:
        logger.info("Creating IPtables DROP rule to the outside world for VNet interface {}".format(ifname))
        try:
            check_call(shlex.split("iptables -A {}".format(rule)))
        except CalledProcessError as e:
            logger.error("Unable to create IPtables rule, got output: {}".format(e.output))


def configure_vnet_interface(ifname):
    """
    Configures an vnet interface to be in the correct state for forwarding vnet machine traffic
    :param str ifname: The vnet interface to configure
    """
    ip = IPRoute()
    dev = ip.link_lookup(ifname=ifname)[0]
    # Make sure it's set to down state
    ip.link("set", index=dev, state="down")
    # Set the mac
    ip.link("set", index=dev, address=random_mac_generator())
    # Finally, bring up the interface
    ip.link("set", index=dev, state="up")


def configure_veth_interface(name, data):
    """
    Configures a veth interface, connects to the correct bridge
    :param str name: The name of the veth interface
    :param dict data: The veth interface data (bridge name)
    """
    logger.info("Creating VNet veth interface {}".format(name))
    ip = IPRoute()
    dev = ip.link_lookup(ifname=name)[0]
    bridge = ip.link_lookup(ifname=data["bridge"])[0]
    # Connect the veth interface to the bridge
    ip.link("set", index=dev, master=bridge)


def bring_up_vnet_interfaces(config, sniffer=False):
    """
    Check the status of the vnet interfaces defined in the config and brings up the interfaces if needed
    :param dict config: The config generated by get_config()
    :param bool sniffer: Check for a sniffer process and create it if it does not exist
    """
    ip = IPRoute()
    for ifname in get_vnet_interface_names_from_config(config):
        if not check_if_interface_exists(ifname):
            create_vnet_interface(ifname)
        # Block traffic to the outside world
        create_vnet_interface_iptables_rules(ifname)
        # Make sure the interface is up
        ip.link("set", ifname=ifname, state="up")
        if sniffer and not check_if_sniffer_exists(ifname):
            # Create it
            start_tcpdump_on_vnet_interface(ifname)
    if "veths" in config:
        ensure_vnet_veth_interfaces(config)


def ensure_vnet_veth_interfaces(config):
    """
    Create en configure the veth interfaces defined in the VNet config
    Assumes there are veth interfaces present in the config
    :param dict config: The config generated by get_config()
    """
    logger.info("VNet veth config found, ensuring interfaces")
    for name, data in config["veths"].items():
        # Set STP on the master if required
        if "stp" in data:
            logger.info("{} STP on VNet interface {}".format("Enabling" if data["stp"] else "Disabling", data["bridge"]))
            state = 1 if data["stp"] else 0
            nbd = NDB(log=False)
            with nbd.interfaces[data["bridge"]] as bridge:
                bridge.set("br_stp_state", state)
        if not check_if_interface_exists(name):
            create_veth_interface(name, data)
        # Always configure a VNet veth interface to make sure it is connected to its master bridge
        configure_veth_interface(name, data)
        configure_vnet_interface(name)


def check_if_sniffer_exists(ifname):
    """
    Check if there is already a sniffer running for a VNet interface
    :param str ifname: The VNet interface name to check
    :return bool: True if it exists, False otherwise
    """
    for process in process_iter():
        process_line = process.cmdline()
        if "tcpdump" in process_line and ifname in process_line:
            logger.debug("A TCPdump sniffer for interface {} already exists".format(ifname))
            return True
    return False


def bring_down_vnet_interfaces(config):
    """
    Brings down the VNet interfaces defined in the config
    This will automatically kill any attached sniffer processes
    :param dict config: The config generated by get_config()
    """
    ip = IPRoute()
    if "veths" in config:
        for name in config["veths"].keys():
            if check_if_interface_exists(name):
                logger.info("Bringing down VNet veth interface {}".format(name))
                ip.link("set", ifname=name, state="down")
    for ifname in get_vnet_interface_names_from_config(config):
        # Set the interface to down status
        if check_if_interface_exists(ifname):
            logger.info("Bringing down VNet interface {}".format(ifname))
            ip.link("set", ifname=ifname, state="down")
        else:
            # Device doesn't exist
            logger.warning("Tried to bring down VNet interface {}, but the interface doesn't exist".format(ifname))


def delete_vnet_interfaces(config):
    """
    Delete the VNet interfaces defined in the config
    :param config:
    :return:
    """
    ip = IPRoute()
    if "veths" in config:
        for name, data in config["veths"].items():
            # Veth interfaces are deleted in pairs, so we only delete the ones with a peer
            if "peer" in data and check_if_interface_exists(name):
                logger.info("Deleting VNet veth interface {}".format(name))
                ip.link("del", ifname=name)
    for ifname in get_vnet_interface_names_from_config(config):
        # Delete the interface
        if check_if_interface_exists(ifname):
            logger.info("Deleting VNet interface {}".format(ifname))
            ip.link("del", ifname=ifname)
        else:
            # Device doesn't exist
            logger.info("Tried to delete VNet interface {}, but it is already gone. That's okay".format(ifname))


def start_tcpdump_on_vnet_interface(ifname):
    """
    Starts a tcpdump process on a vnet interface
    :param str ifname: The interface to start the tcpdump on
    """
    path = join(settings.VNET_SNIFFER_PCAP_DIR, "{}.pcap".format(ifname))
    logger.info("Starting sniffer on VNet interface {}, PCAP location: {}".format(ifname, path))
    Popen(shlex.split("tcpdump -i {} -U -w {}".format(ifname, path)))
