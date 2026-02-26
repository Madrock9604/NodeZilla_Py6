import os
import sys
from pathlib import Path
from .paths import user_pl_path

programming_delay = 10


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
                        Component_Attributes = line.split(" ")
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
                        Compline = line.split(" ")
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
            "Q": "Transistor"
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

    def __init__(self, Used_Components, backend):
        #Write section of script to reset everything upon script run
        CirToScript.Reset(backend)
        #Write Section that connects Ground to node 0 (Multisim default value for ground)
        CirToScript.ConnectGround(backend)
        #For each used component
        for component in Used_Components:
            #For each terminal each used component has
            for pin in component.pin:
                #Translate each port and line to binary and write it to the file
                CirToScript.ConnectPortToNet(backend, pin.port, pin.net)
                pass
            pass
        pass
        #Turn all the IOs off
        CirToScript.TurnIOoff(backend)

    def Reset(backend):
        ok, msg = backend.RESET(programming_delay)

    def ConnectGround(backend):
        GroundPort = 2
        GroundNet = 0
        ok, msg = backend.PORT(GroundPort)
        ok, msg = backend.LINE(GroundNet)
        ok, msg = backend.STROBE(programming_delay)

    def ConnectPortToNet(backend, Port, Net):
        ok, msg = backend.PORT(int(Port))
        ok, msg = backend.LINE(int(Net))
        ok, msg = backend.STROBE(programming_delay)
        pass

    def TurnIOoff(backend):
        ok, msg = backend.PORT(int(0))
        ok, msg = backend.LINE(int(0))
        ok, msg = backend.STROBE(programming_delay)
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
