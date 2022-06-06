from ipaddress import IPv4Interface, IPv6Interface, ip_interface, ip_network, ip_address
from re import fullmatch
from logging import getLogger
from os.path import isdir, isfile, join
from copy import deepcopy

from vnet_manager.utils.mac import random_mac_generator
from vnet_manager.conf import settings

logger = getLogger(__name__)


class ValidateConfig:
    """
    Validates the config generated by get_config() and updates some values if missing
    """

    def __init__(self, config: dict):
        """
        :param dict config: The config generated by get_config()
        """
        self._all_ok = True
        self._validators_ran = 0
        self._new_config = deepcopy(config)
        self.default_message = ". Please check your settings"
        self.config = config

    def __str__(self) -> str:
        return "VNet config validator, current_state: {}, amount of validators run: {}".format(
            "OK" if self._all_ok else "NOT OK", self._validators_ran
        )

    @property
    def config_validation_successful(self) -> bool:
        """
        This property can be called to see if any unrecoverable errors in the config have been found
        """
        return self._all_ok

    @property
    def updated_config(self) -> dict:
        """
        This property contains a updated config dict, with all values that have been fixed by this validator
        """
        return self._new_config

    @property
    def validators_ran(self) -> int:
        """
        Return the amount of validators that have been run
        """
        return self._validators_ran

    def validate(self):
        """
        Run all validation functions
        """
        self._all_ok = True
        self.validate_switch_config()
        self.validate_machine_config()
        if "veths" in self.config:
            self.validate_veth_config()

    def validate_switch_config(self):
        """
        Validates the switch part of the config
        """
        self._validators_ran += 1
        if "switches" not in self.config:
            logger.error(f"Config item 'switches' missing{self.default_message}")
            self._all_ok = False
        elif not isinstance(self.config["switches"], int):
            logger.error(f"Config item 'switches: {self.config['switches']}' does not seem to be an integer{self.default_message}")
            self._all_ok = False

    def validate_machine_config(self):
        # TODO: Refactor
        # pylint: disable=too-many-branches
        """
        Validates the machines part of the config
        """
        self._validators_ran += 1
        if "machines" not in self.config:
            logger.error(f"Config item 'machines' missing{self.default_message}")
            self._all_ok = False
        elif not isinstance(self.config["machines"], dict):
            logger.error(f"Machines config is not a dict, this means the user config is incorrect{self.default_message}")
            self._all_ok = False
        else:
            for name, values in self.config["machines"].items():
                if "type" not in values:
                    logger.error(f"Type not found for machine {name}{self.default_message}")
                    self._all_ok = False
                elif values["type"] not in settings.SUPPORTED_MACHINE_TYPES:
                    logger.error(
                        "Type {} for machine {} unsupported. I only support the following types: {}{}".format(
                            values["type"], name, settings.SUPPORTED_MACHINE_TYPES, self.default_message
                        )
                    )
                    self._all_ok = False

                # Files
                if "files" in values:
                    if not isinstance(values["files"], dict):
                        logger.error(f"Files directive for machine {name} is not a dict{self.default_message}")
                        self._all_ok = False
                    else:
                        # Check the files
                        self.validate_machine_files_parameters(name)

                # Interfaces
                if "interfaces" not in values:
                    logger.error(f"Machine {name} does not appear to have any interfaces{self.default_message}")
                    self._all_ok = False
                elif not isinstance(values["interfaces"], dict):
                    logger.error(
                        "The interfaces for machine {} are not given as a dict, this usually means a typo in the config{}".format(
                            name, self.default_message
                        )
                    )
                    self._all_ok = False
                else:
                    self.validate_interface_config(name)

                # VLANs?
                if "vlans" not in values:
                    logger.debug(f"Machine {name} does not appear to have any VLAN interfaces, that's okay")
                elif not isinstance(values["vlans"], dict):
                    logger.error(
                        "Machine {} has a VLAN config but it does not "
                        "appear to be a dict, this usually means a typo in the config{}".format(name, self.default_message)
                    )
                    self._all_ok = False
                else:
                    self.validate_vlan_config(name)

                # Bridges?
                if "bridges" not in values:
                    logger.debug(f"Machine {name} does not appear to have any Bridge interfaces, that's okay")
                elif not isinstance(values["bridges"], dict):
                    logger.error(
                        "Machine {} has a bridge config defined, but it is not a dictionary, "
                        "this usally means a typo in the config{}".format(name, self.default_message)
                    )
                    self._all_ok = False
                else:
                    self.validate_machine_bridge_config(name)

    def validate_vlan_config(self, machine):
        """
        Validates the VLAN config of a particular machine
        :param machine: str: the machine to validate the VLAN config for
        """
        vlans = self.config["machines"][machine]["vlans"]
        for name, values in vlans.items():
            if "id" not in values:
                logger.error(f"VLAN {name} on machine {machine} is missing it's vlan id{self.default_message}")
                self._all_ok = False
            else:
                try:
                    self._new_config["machines"][machine]["vlans"][name]["id"] = int(values["id"])
                except ValueError:
                    logger.error(
                        f"Unable to cast VLAN {name} with ID {values['id']} from machine {machine} to a integer{self.default_message}"
                    )
                    self._all_ok = False
            if "link" not in values:
                logger.error(f"VLAN {name} on machine {machine} is missing it's link attribute{self.default_message}")
                self._all_ok = False
            elif not isinstance(values["link"], str):
                logger.error(
                    f"Link {values['link']} for VLAN {name} on machine {machine}, does not seem to be a string{self.default_message}"
                )
                self._all_ok = False
            # This check requires a valid interface config, so we only do it if the previous checks have been successful
            elif self._all_ok and values["link"] not in self.config["machines"][machine]["interfaces"]:
                logger.error(
                    "Link {} for VLAN {} on machine {} does not correspond to any interfaces on the same machine{}".format(
                        values["link"], name, machine, self.default_message
                    )
                )
                self._all_ok = False
            if "addresses" not in values:
                logger.debug(f"VLAN {name} on machine {machine} does not have any addresses, that's okay")
            elif not isinstance(values["addresses"], list):
                logger.error(f"Addresses on VLAN {name} for machine {machine}, does not seem to be a list{self.default_message}")
                self._all_ok = False
            else:
                for address in values["addresses"]:
                    try:
                        ip_interface(address)
                    except ValueError as e:
                        logger.error(
                            "Address {} for VLAN {} on machine {} does not seem to be a valid address, got parse error {}".format(
                                address, name, machine, e
                            )
                        )
                        self._all_ok = False

    def validate_machine_files_parameters(self, machine: str):
        """
        Validates the files config of a particular machine
        Assumes the files dict exists for that machine
        :param str machine: The machine to validates the files config for
        """
        files = self.config["machines"][machine]["files"]
        for host_file in files.keys():
            # First check if the user gave a relative dir from the config dir
            if isdir(join(self.config["config_dir"], host_file)) or isfile(join(self.config["config_dir"], host_file)):
                logger.debug(f"Updating relative host_file path {host_file} to full path {join(self.config['config_dir'], host_file)}")
                self._new_config["machines"][machine]["files"][join(self.config["config_dir"], host_file)] = self._new_config["machines"][
                    machine
                ]["files"].pop(host_file)
            # Check for absolute paths
            elif not isdir(host_file) or not isfile(host_file):
                logger.error(f"Host file {host_file} for machine {machine} does not seem to be a dir or a file{self.default_message}")
                self._all_ok = False

    def validate_interface_config(self, machine: str):
        # TODO: Refactor
        # pylint: disable=too-many-branches
        """
        Validates the interface config of a particular machine
        Assumes the interfaces dict exists for that machine
        :param str machine: the machine to validate the interfaces config for
        """
        interfaces = self.config["machines"][machine]["interfaces"]
        for int_name, int_vals in interfaces.items():
            if "ipv4" not in int_vals:
                logger.debug(f"No IPv4 found for interface {int_name} on machine {machine}. That's okay, no IPv4 will be configured")
            else:
                # Validate the given IP
                try:
                    IPv4Interface(int_vals["ipv4"])
                except ValueError as e:
                    logger.error(f"Unable to parse IPv4 address {int_vals['ipv4']} for machine {machine}. Parse error: {e}")
                    self._all_ok = False
            if "ipv6" not in int_vals:
                logger.debug(f"No IPv6 found for interface {int_name} on machine {machine}, that's okay no IPv6 address will be configured")
            else:
                # Validate the given IP
                try:
                    IPv6Interface(int_vals["ipv6"])
                except ValueError as e:
                    logger.error(f"Unable to parse IPv6 address {int_vals['ipv6']} for machine {machine}. Parse error: {e}")
                    self._all_ok = False
            if "mac" not in int_vals:
                logger.debug(f"MAC not found for interface {int_name} on machine {machine}, generating a random one")
                self._new_config["machines"][machine]["interfaces"][int_name]["mac"] = random_mac_generator()
            # From: https://stackoverflow.com/a/7629690/8632038
            elif not fullmatch(r"^([0-9A-Fa-f]{2}[:-]){5}([0-9A-Fa-f]{2})$", int_vals["mac"]):
                logger.error(
                    f"MAC {int_vals['mac']} for interface {int_name} on machine {machine}, does not seem to be valid{self.default_message}"
                )
                self._all_ok = False
            if "bridge" not in int_vals:
                logger.error(f"bridge keyword missing on interface {int_name} for machine {machine}{self.default_message}")
                self._all_ok = False
            elif not isinstance(int_vals["bridge"], int) or int_vals["bridge"] > self.config["switches"] - 1:
                logger.error(
                    "Invalid bridge number detected for interface {} on machine {}. "
                    "The bridge keyword should correspond to the interface number of the vnet bridge to connect to "
                    "(starting at iface number 0)".format(int_name, machine)
                )
                self._all_ok = False
            if "routes" in int_vals:
                if not isinstance(int_vals["routes"], list):
                    logger.error(
                        "routes passed to interface {} for machine {}, found type {}, expected type 'list'{}".format(
                            int_name, machine, type(int_vals["routes"]).__name__, self.default_message
                        )
                    )
                    self._all_ok = False
                else:
                    self.validate_interface_routes(int_vals["routes"], int_name, machine)

    def validate_interface_routes(self, routes: list, int_name: str, machine: str):
        for idx, route in enumerate(routes):
            if "to" not in route:
                logger.error(
                    f"'to' keyword missing from route {idx + 1} on interface {int_name} for machine {machine}{self.default_message}"
                )
                self._all_ok = False
            else:
                try:
                    ip_network(route["to"])
                except ValueError:
                    if route["to"] == "default":
                        logger.debug(
                            "Updating 'default' to destination for route {} on interface {} for machine "
                            "{} to 0.0.0.0/0 for backwards compatibility".format(idx + 1, int_name, machine)
                        )
                        self._new_config["machines"][machine]["interfaces"][int_name]["routes"][idx]["to"] = "0.0.0.0/0"
                    else:
                        logger.error(
                            "Invalid 'to' value {} for route {} on interface {} for machine {}{}".format(
                                route["to"], idx + 1, int_name, machine, self.default_message
                            )
                        )
                        self._all_ok = False
            if "via" not in route:
                logger.error(
                    f"'via' keyword missing from route {idx + 1} on interface {int_name} for machine {machine}{self.default_message}"
                )
                self._all_ok = False
            else:
                try:
                    ip_address(route["via"])
                except ValueError:
                    logger.error(
                        "Invalid 'via' value {} (not an IP address) for route {} on interface {} for machine {}{}".format(
                            route["via"], idx + 1, int_name, machine, self.default_message
                        )
                    )
                    self._all_ok = False

    def validate_machine_bridge_config(self, machine: str):
        bridges = self.config["machines"][machine]["bridges"]
        for br_name, br_vals in bridges.items():
            if "ipv4" not in br_vals:
                logger.debug(f"Bridge {br_name} on machine {machine} has no IPv4 assigned, that's okay")
            else:
                # Validate the given IP
                try:
                    IPv4Interface(br_vals["ipv4"])
                except ValueError as e:
                    logger.error(f"Unable to parse IPv4 address for bridge {br_name} on machine {machine}, got error: {e}")
                    self._all_ok = False
            if "ipv6" not in br_vals:
                logger.debug(f"Bridge {br_name} on machine {machine} has no IPv6 address, that's okay")
            else:
                try:
                    # Validate the IPv6 address
                    IPv6Interface(br_vals["ipv6"])
                except ValueError as e:
                    logger.error(f"Unable to parse IPv6 address for bridge {br_name} on machine {machine}, got error: {e}")
                    self._all_ok = False
            if "slaves" not in br_vals:
                logger.error(f"Bridge {br_name} on machine {machine} does not have any slaves")
                self._all_ok = False
            elif not isinstance(br_vals["slaves"], list):
                logger.error(f"Slaves on bridge {br_name} for machine {machine}, is not formatted as a list")
                self._all_ok = False
            else:
                # For each slave, check if the interface exists
                for slave in br_vals["slaves"]:
                    if slave not in self.config["machines"][machine]["interfaces"].keys():
                        logger.error(f"Undefined slave interface {slave} assigned to bridge {br_name} on machine {machine}")
                        self._all_ok = False

    def validate_veth_config(self):
        """
        Validates the veth config if present
        """
        if "veths" not in self.config:
            logger.warning("Tried to validate veth config, but no veth config present, skipping...")
            return
        if not isinstance(self.config["veths"], dict):
            logger.error(f"Config item: 'veths' does not seem to be a dict {self.default_message}")
            self._all_ok = False
            return
        for name, values in self.config["veths"].items():
            if not isinstance(name, str):
                logger.error(f"veth interface name: {name} does not seem to be a string{self.default_message}")
                self._all_ok = False
            elif not isinstance(values, dict):
                logger.error(f"veth interface {name} data does not seem to be a dict{self.default_message}")
                self._all_ok = False
            else:
                if "bridge" not in values:
                    logger.error(f"veth interface {name} is missing the bridge parameter{self.default_message}")
                    self._all_ok = False
                elif not isinstance(values["bridge"], str):
                    logger.error(f"veth interface {name} bridge parameter does not seem to be a str{self.default_message}")
                    self._all_ok = False
                if "peer" not in values:
                    logger.debug(f"veth interface {name} does not have a peer, that's ok, assuming it's peer is defined elsewhere")
                elif not isinstance(values["peer"], str):
                    logger.error(f"veth interface {name} peer parameter does not seem to be a string{self.default_message}")
                    self._all_ok = False
                if "stp" not in values:
                    logger.debug(f"veth interface {name} as no STP parameter, that's okay")
                elif not isinstance(values["stp"], bool):
                    logger.error(f"veth interface {name} stp parameter does not seem to be a boolean{self.default_message}")
                    self._all_ok = False
