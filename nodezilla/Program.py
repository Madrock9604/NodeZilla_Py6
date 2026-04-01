import os
import sys
import time
from pathlib import Path
from .paths import user_pl_path

programming_delay = 10
MAX_RUNTIME_LINES = 15
ENABLE_RUNTIME_GROUND_CONNECT = os.environ.get("NODEZILLA_ENABLE_RUNTIME_GROUND_CONNECT", "1").strip().lower() not in {
    "0",
    "false",
    "no",
    "off",
}
RUNTIME_SUPPLY_SOURCE_PORTS = {
    "5V+": 0,
    "5V-": 1,
}


def _normalize_runtime_net_name(net) -> str:
    token = str(net or "").strip()
    upper = token.upper()
    if upper in {"", "OPEN", "NC", "N/C", "NONE", "NA"}:
        return "OPEN"
    if upper in {"0", "GND", "GROUND"}:
        return "0"
    return upper


class RuntimeLineAllocator:
    """Assign logical net names onto the finite hardware line pool.

    Ground is always line 0. Numbered nets in 1..15 keep their number when
    possible for backward compatibility; named nets are assigned the next free
    physical line. OPEN/NC pins do not consume lines.
    """

    def __init__(self, max_lines: int = MAX_RUNTIME_LINES):
        self.max_lines = max(1, int(max_lines or MAX_RUNTIME_LINES))
        self._net_to_line: dict[str, int] = {"0": 0}
        self._line_to_net: dict[int, str] = {0: "0"}

    def reserve(self, net) -> int | None:
        name = _normalize_runtime_net_name(net)
        if name == "OPEN":
            return None
        existing = self._net_to_line.get(name)
        if existing is not None:
            return existing

        requested: int | None = None
        if name.isdigit():
            requested = int(name)
            if requested < 0 or requested > self.max_lines:
                raise RuntimeError(
                    f"Net '{net}' requests hardware line {requested}, "
                    f"but only lines 0..{self.max_lines} exist."
                )
            if requested == 0:
                self._net_to_line[name] = 0
                self._line_to_net[0] = name
                return 0

        if requested is not None:
            owner = self._line_to_net.get(requested)
            if owner is None or owner == name:
                self._net_to_line[name] = requested
                self._line_to_net[requested] = name
                return requested
            raise RuntimeError(
                f"Hardware line {requested} is already reserved for net '{owner}', "
                f"so net '{net}' cannot also use it."
            )

        for candidate in range(1, self.max_lines + 1):
            if candidate not in self._line_to_net:
                self._net_to_line[name] = candidate
                self._line_to_net[candidate] = name
                return candidate
        raise RuntimeError(
            f"Too many unique nets for runtime hardware: only {self.max_lines} "
            f"non-ground lines are available."
        )

    def line_for(self, net) -> int | None:
        return self.reserve(net)

    def mapping(self) -> dict[str, int]:
        return dict(self._net_to_line)

    @classmethod
    def from_components(cls, used_components, max_lines: int = MAX_RUNTIME_LINES):
        alloc = cls(max_lines=max_lines)
        numeric_nets: list[str] = []
        named_nets: list[str] = []
        for component in used_components:
            for pin in getattr(component, "pin", []):
                name = _normalize_runtime_net_name(getattr(pin, "net", None))
                if name == "OPEN":
                    continue
                if name == "0":
                    alloc.reserve(name)
                elif name.isdigit():
                    numeric_nets.append(name)
                else:
                    named_nets.append(name)
        for name in numeric_nets:
            alloc.reserve(name)
        for name in named_nets:
            alloc.reserve(name)
        return alloc


def _pl_read_candidates() -> list[Path]:
    """Return candidate locations for PL.txt in priority order."""
    out: list[Path] = []
    # Primary user-writable location.
    out.append(user_pl_path())
    env_path = os.environ.get("NODEZILLA_PL_PATH", "").strip()
    if env_path:
        out.append(Path(env_path).expanduser())
    # Typical dev run location.
    out.append(Path.cwd() / "PL.txt")
    # Repo root relative to nodezilla/Program.py.
    out.append(Path(__file__).resolve().parent.parent / "PL.txt")
    # PyInstaller bundle extraction path.
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        out.append(Path(meipass) / "PL.txt")
    # macOS .app run paths.
    exe = Path(sys.executable).resolve()
    out.append(exe.parent / "PL.txt")
    out.append(exe.parent.parent / "Resources" / "PL.txt")
    out.append(exe.parent.parent.parent / "PL.txt")
    # User-writable fallback.
    out.append(Path.home() / "Library" / "Application Support" / "NodeZilla" / "PL.txt")
    # De-duplicate while preserving order.
    uniq = []
    seen = set()
    for p in out:
        s = str(p)
        if s in seen:
            continue
        seen.add(s)
        uniq.append(p)
    return uniq


def _resolve_pl_for_read() -> Path | None:
    for p in _pl_read_candidates():
        try:
            if p.exists() and p.is_file():
                return p
        except Exception:
            continue
    return None


def _resolve_pl_for_write() -> Path:
    env_path = os.environ.get("NODEZILLA_PL_PATH", "").strip()
    if env_path:
        p = Path(env_path).expanduser()
        p.parent.mkdir(parents=True, exist_ok=True)
        return p
    p = user_pl_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


class CreateComponentDataSet:
    def MakeDataSet():
        """This might need to be updated to account several scenarios"""
        #Line ignore prefixes
        ignore_prefix = ['*', ".", "v", "I", "R", "\n"]
        ComponentDataSet = []
        
        #Open Portlist file
        pl_path = _resolve_pl_for_read()
        if pl_path is None:
            tried = "\n".join(str(p) for p in _pl_read_candidates())
            print(
                "Error 02: Portlist not found. Expected PL.txt in one of:\n"
                f"{tried}\n"
                "Tip: set NODEZILLA_PL_PATH to override."
            )
            return ComponentDataSet
        try:
            with open(pl_path, 'r') as PL:
                Componentid = 0
                for line in PL:
                    if not line.startswith(tuple(ignore_prefix)):
                        Component_Attributes = line.split()
                        ComponentDataSet.append(Component(Componentid, Component_Attributes))
                        Componentid += 1
                        pass
                pass
        except FileNotFoundError:
            print(f"Error 02: Portlist not found: {pl_path}")
        return ComponentDataSet

class ComponentSerach:
    def __init__(self, ComponentDataSet, NetlistFile):
        """This might need to be updated to account several scenarios"""
        #Line ignore prefixes
        ignore_prefix = ['*', ".", "v", "I", "\n"]
        #Open Netlist File
        try:
            with open(NetlistFile, "r") as File:
                for line in File:
                    if not line.startswith(tuple(ignore_prefix)):
                        #Here we should start populating the PL Component Dataset
                        #with the nets required by the netlist
                        Compline = line.split()
                        Requested_Component = Component(0, Compline)
                        ComponentSerach.SearchComponent(Requested_Component, ComponentDataSet)
                        pass
                    pass
        except FileNotFoundError:
            print("Error 01: File not found. Please check the file path and ensure it exists")
        pass
    pass

    def SearchComponent(Requested_Component, ComponentDataSet):
        Componentfound = False
        for Component in ComponentDataSet:
            if Requested_Component.type == "Resistor" or Requested_Component.type == "Capacitor" or Requested_Component.type == "Inductor":
                if Requested_Component.type == Component.type and Requested_Component.value == Component.value and not(Component.used):
                    Componentfound = True
                    ComponentSerach.AssignNetToPin(Component, Requested_Component)
                pass
            elif Requested_Component.type == "Instrument":
                if Requested_Component.name == Component.name and not(Component.used):
                    Componentfound = True
                    ComponentSerach.AssignNetToPin(Component, Requested_Component)
            else:
                if Requested_Component.type == Component.type and Requested_Component.partnum == Component.partnum:
                    Componentfound = True
                    ComponentSerach.AssignNetToPin(Component, Requested_Component)
                    pass
            if Componentfound: break
        pass

    def AssignNetToPin(Component, Requested_Component):
        pinnumber = 0
        Component.used = True
        for pin in Component.pin:
            pin.net = Requested_Component.pin[pinnumber].port
            pinnumber += 1
            pass
        pass

    def GetComponentsUsed(ComponentDataSet):
        Used_Components = []
        for Component in ComponentDataSet:
            if Component.used:
                Used_Components.append(Component)
            else:
                continue
        return Used_Components

class Component:
    
    def __init__(self, ID, CompLine):
        #IMPORTANT NOTE: System differentiate Meters than Scopes by using the name
        ComponentTypes = {
            "r": "Resistor",
            "R": "Resistor",
            "c": "Capacitor",
            "C": "Capacitor",
            "l": "Inductor",
            "L": "Inductor",
            "d": "Diode",
            "D": "Diode",
            "X": "Instrument",
            "x": "Op-Amp",
            "Q": "Transistor",
            "q": "Transistor"
        }
        
        self.ID = ID
        self.type = ComponentTypes[CompLine[0][0]]
        self.name = CompLine[0][1:]
        self.pin = []
        if(self.type == "Resistor" or self.type == "Capacitor" or self.type == "Inductor"):
            self.value = float(CompLine[3])
            self.partnum = None
            self.pin.append(Ports(CompLine[1]))
            self.pin.append(Ports(CompLine[2]))
            pass
        else:
            self.value = "NA"
            self.partnum = CompLine[len(CompLine)-1]
            if self.partnum[-1] == "\n":
                self.partnum = self.partnum[:-1]
            for x in range(1, len(CompLine)-1):
                self.pin.append(Ports(CompLine[x]))
            pass
            pass
        self.used = False
        
        pass
    
    pass

class Ports:
    
    def __init__(self, port, net = None, safe = True):
        self.safe = safe
        self.port = port
        self.net = net
        pass
    pass

class CirToScript:

    def __init__(self, Used_Components, backend, logger=None):
        self._logger = logger
        self._allocator = RuntimeLineAllocator.from_components(Used_Components)
        if logger is not None:
            for name, line in sorted(self._allocator.mapping().items(), key=lambda kv: (kv[1], kv[0])):
                logger(f"line-map logical_net={name} physical_line={line}")
        self._log_backend_state(backend, "build-start")
        #Write section of script to reset everything upon script run
        CirToScript.Reset(backend, logger=logger)
        #Write Section that connects Ground to node 0 (Multisim default value for ground)
        if ENABLE_RUNTIME_GROUND_CONNECT:
            CirToScript.ConnectGround(backend, logger=logger)
        elif logger is not None:
            logger("ground-connect skipped by NODEZILLA_ENABLE_RUNTIME_GROUND_CONNECT")
        # Reserved hardware supplies such as 5V+ / 5V- can be tied to their
        # dynamically assigned logical lines here.
        CirToScript.ConnectReservedSupplyLines(backend, self._allocator, logger=logger)
        #For each used component
        for component in Used_Components:
            #For each terminal each used component has
            for pin_idx, pin in enumerate(component.pin, start=1):
                physical_line = self._allocator.line_for(pin.net)
                if physical_line is None:
                    if logger is not None:
                        logger(
                            "component-connect skipped-open "
                            f"type={component.type} name={component.name} pin_index={pin_idx} "
                            f"port={pin.port} net={pin.net}"
                        )
                    continue
                #Translate each port and line to binary and write it to the file
                if logger is not None:
                    logger(
                        "component-connect "
                        f"type={component.type} name={component.name} pin_index={pin_idx} "
                        f"port={pin.port} net={pin.net} physical_line={physical_line}"
                    )
                CirToScript.ConnectPortToNet(backend, pin.port, physical_line, logger=logger)
                pass
            pass
        pass
        #Turn all the IOs off
        CirToScript.TurnIOoff(backend, logger=logger)
        self._log_backend_state(backend, "build-end")

    @staticmethod
    def _read_supplies_snapshot(backend):
        try:
            ok, _msg, st = backend.read_supplies_status()
            if ok and isinstance(st, dict):
                return {
                    "vp_set": float(st.get("v_pos_v", 0.0)),
                    "vn_set": float(st.get("v_neg_v", 0.0)),
                    "vp_meas": float(st.get("v_pos_meas_v", st.get("v_pos_v", 0.0))),
                    "vn_meas": float(st.get("v_neg_meas_v", st.get("v_neg_v", 0.0))),
                    "usb_v": float(st.get("usb_voltage_v", 0.0)),
                    "usb_i": float(st.get("usb_current_a", 0.0)),
                    "temp_c": float(st.get("temperature_c", 0.0)),
                    "master": bool(st.get("master_enabled", False)),
                }
        except Exception:
            pass
        return None

    @staticmethod
    def _read_dio_snapshot(backend):
        try:
            ok, _msg, mask = backend.digitalio_read_mask()
            if ok:
                return int(mask)
        except Exception:
            pass
        return None

    @staticmethod
    def _log_backend_state(backend, label, logger=None):
        if logger is None:
            return
        snap = CirToScript._read_supplies_snapshot(backend)
        dio = CirToScript._read_dio_snapshot(backend)
        parts = [f"state={label}"]
        if snap is not None:
            parts.extend(
                [
                    f"master={int(snap['master'])}",
                    f"vp_set={snap['vp_set']:.3f}",
                    f"vp_meas={snap['vp_meas']:.3f}",
                    f"vn_set={snap['vn_set']:.3f}",
                    f"vn_meas={snap['vn_meas']:.3f}",
                    f"usb_v={snap['usb_v']:.3f}",
                    f"usb_i={snap['usb_i']:.3f}",
                    f"temp_c={snap['temp_c']:.1f}",
                ]
            )
        if dio is not None:
            parts.append(f"dio=0x{dio:04X}")
        logger(" ".join(parts))

    @staticmethod
    def _run_hw_step(backend, label, op_name, value, logger=None):
        CirToScript._log_backend_state(backend, f"{label}-pre", logger=logger)
        fn = getattr(backend, op_name)
        ok, msg = fn(value)
        if logger is not None:
            logger(f"op={op_name} label={label} value={value} ok={int(bool(ok))} msg={msg}")
        # Let AnalogIO telemetry settle enough to catch a rail collapse.
        time.sleep(0.02)
        CirToScript._log_backend_state(backend, f"{label}-post", logger=logger)
        return ok, msg

    def Reset(backend, logger=None):
        CirToScript._log_backend_state(backend, "reset-sequence-pre", logger=logger)
        ok, msg = backend.RESET(programming_delay)
        if logger is not None:
            logger(f"op=RESET delay_ms={programming_delay} ok={int(bool(ok))} msg={msg}")
        time.sleep(0.02)
        CirToScript._log_backend_state(backend, "reset-sequence-post", logger=logger)

    def ConnectGround(backend, logger=None):
        GroundPort = 2
        GroundNet = 0
        CirToScript._run_hw_step(backend, "ground-port", "PORT", GroundPort, logger=logger)
        CirToScript._run_hw_step(backend, "ground-line", "LINE", GroundNet, logger=logger)
        CirToScript._run_hw_step(backend, "ground-strobe", "STROBE", programming_delay, logger=logger)

    def ConnectReservedSupplyLines(backend, allocator, logger=None):
        """Hook point for reserved hardware-backed supply nets.

        Right now this only reports which physical line each named supply net
        received. To make the hardware connection active, this is the exact
        place to call ConnectPortToNet for those nets.
        """
        mapping = allocator.mapping() if hasattr(allocator, "mapping") else {}
        for logical_net, source_port in RUNTIME_SUPPLY_SOURCE_PORTS.items():
            physical_line = mapping.get(logical_net)
            if physical_line is None:
                continue
            if logger is not None:
                logger(
                    "reserved-supply-line "
                    f"logical_net={logical_net} source_port={source_port} physical_line={physical_line}"
                )
            # When you want 5V+ / 5V- to drive the reserved net directly,
            # enable the line below:
            CirToScript.ConnectPortToNet(backend, source_port, physical_line, logger=logger)

    def ConnectPortToNet(backend, Port, Net, logger=None):
        CirToScript._run_hw_step(backend, f"connect-port-{Port}", "PORT", int(Port), logger=logger)
        CirToScript._run_hw_step(backend, f"connect-line-{Net}", "LINE", int(Net), logger=logger)
        CirToScript._run_hw_step(backend, f"connect-strobe-{Port}-{Net}", "STROBE", programming_delay, logger=logger)
        pass

    def TurnIOoff(backend, logger=None):
        # After programming completes we only want address/control lines idle.
        # Do not reset the matrix here or the built circuit disappears.
        try:
            ok, msg = backend.digitalio_write_mask(0)
            if logger is not None:
                logger(f"op=digitalio_write_mask label=iooff-mask value=0 ok={int(bool(ok))} msg={msg}")
        except Exception as e:
            if logger is not None:
                logger(f"op=digitalio_write_mask label=iooff-mask value=0 ok=0 msg={e}")
        CirToScript._log_backend_state(backend, "iooff-post", logger=logger)
        pass

    def BinaryXAddress(Port):
        Xaddress = int(Port)
        BinaryForm = format(Xaddress, 'b')
        XBits = ['0']*8
        for x in range(len(BinaryForm)):
            XBits[x-len(BinaryForm)] = BinaryForm[x]
        return XBits
    
    def BinaryYAddress(Net):
        Yaddress = int(Net)
        BinaryForm = format(Yaddress, 'b')
        YBits = ['0']*4
        for x in range(len(BinaryForm)):
            YBits[x-len(BinaryForm)] = BinaryForm[x]
        return YBits
    
class CreatePortlist:

    def __init__(self, ResourceFiles):
        #Create a new empty PL file
        pl_path = _resolve_pl_for_write()
        with open(pl_path, 'w') as PortListFile:
            #Number to keep track the number of board being checked
            BoardNumber = 1
            #For each file requested in the set up tab
            for file in ResourceFiles:
                #Check if the requested Resource file is not the default text if it is skip it
                if file == "Please select a component card": continue
                #try to open the file if it doesnt exist throw an error
                try:
                    with open(f"{os.getcwd()}/Resources/{file}", 'r') as f:
                        #Check each line and write to the PL file
                        for line in f:
                            Component_line = ""
                            Component_info = line.split(" ")
                            if Component_info[0].startswith(tuple(["r", "c", "l"])):
                                Component_line = f"{Component_info[0]} {((BoardNumber-1)*64)+int(Component_info[1])} {((BoardNumber-1)*64)+int(Component_info[2])} {Component_info[3]}"
                                if Component_info[0][0] == 'r':
                                    Component_line = f"{Component_line}\n"
                                PortListFile.write(Component_line)
                            else:
                                for section in Component_info:
                                    try:
                                        Component_line = Component_line + f"{((BoardNumber-1)*64)+int(section)} "
                                        pass
                                    except:
                                        Component_line = Component_line + f"{section} "
                                        pass
                                PortListFile.write(Component_line[:-1])
                            pass
                        pass
                except FileNotFoundError:
                    print(f"Resource File: {file} not found. Make sure the file exists")
                BoardNumber +=1
                PortListFile.write("\n")
            pass
        pass

    def get_file_names(directory_path):
        try:
            file_names = [f for f in os.listdir(directory_path) if os.path.isfile(os.path.join(directory_path, f))]
            return file_names
        except FileNotFoundError:
            print(f"Error: Directory not found: {directory_path}")
            return []
        except Exception as e:
            print(f"An error ocurred: {e}")
            return []
