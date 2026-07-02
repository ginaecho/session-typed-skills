"""
Version Control for Multi-Protocol Evolution

Tracks multiple protocols, each with their own version history.
Structure:
    Protocol_1: v0 → v1 → v2 → v3...
    Protocol_2: v0 → v1 → v2...
    Protocol_3: v0 → v1...
"""

import json
from pathlib import Path
from datetime import datetime
from typing import Optional, Dict, List

# All authoring artefacts live under a caller-supplied case_dir (see
# stjp_core/apps/orchestrator.py). The constructor below requires the
# history-file path explicitly — the old module-level VERSION_HISTORY_FILE
# constant was removed when stjp_core/protocols and stjp_core/skills/ were
# retired in favour of experiments/cases/<case>/.


class VersionControl:
    """
    Version control for multiple protocols, each with independent version history.
    
    Data structure:
    {
        "protocols": {
            "1": {
                "name": "ChatProtocol",
                "description": "A chat between client and server",
                "versions": [
                    {"version": 0, "timestamp": "...", "requirement": "...", ...},
                    {"version": 1, "timestamp": "...", "requirement": "...", ...}
                ],
                "current_version": 1
            },
            "2": {
                "name": "AuctionProtocol",
                ...
            }
        },
        "active_protocol_id": "1"
    }
    """
    
    def __init__(self, history_file: Path):
        # Caller must supply a case-scoped history file path (typically
        # <case_dir>/.version_history.json). Parent directories are
        # created lazily on first save.
        self.history_file = history_file
        self.data = self._load_history()
        
    def _load_history(self) -> dict:
        """Load version history from file"""
        if self.history_file.exists():
            try:
                data = json.loads(self.history_file.read_text(encoding='utf-8'))
                # Migrate from old format if needed
                if isinstance(data, list):
                    return self._migrate_old_format(data)
                return data
            except:
                pass
        return {"protocols": {}, "active_protocol_id": None}
    
    def _migrate_old_format(self, old_versions: list) -> dict:
        """Migrate from old linear version format to new multi-protocol format"""
        if not old_versions:
            return {"protocols": {}, "active_protocol_id": None}
        
        # Put all old versions into Protocol 1
        new_data = {
            "protocols": {
                "1": {
                    "name": "Protocol",
                    "description": old_versions[0].get('requirement', 'Migrated protocol'),
                    "versions": [],
                    "current_version": 0
                }
            },
            "active_protocol_id": "1"
        }
        
        for i, v in enumerate(old_versions):
            v['version'] = i  # Re-number starting from 0
            new_data["protocols"]["1"]["versions"].append(v)
            if v.get('is_current', False):
                new_data["protocols"]["1"]["current_version"] = i
        
        return new_data
    
    def _save_history(self):
        """Save version history to file"""
        self.history_file.write_text(
            json.dumps(self.data, indent=2), 
            encoding='utf-8'
        )
    
    def get_next_protocol_id(self) -> str:
        """Get the next available protocol ID"""
        if not self.data["protocols"]:
            return "1"
        return str(max(int(pid) for pid in self.data["protocols"].keys()) + 1)
    
    def create_new_protocol(self, name: str, description: str) -> str:
        """
        Create a new protocol track.
        Returns the protocol ID.
        """
        protocol_id = self.get_next_protocol_id()
        
        self.data["protocols"][protocol_id] = {
            "name": name,
            "description": description,
            "versions": [],
            "current_version": -1,  # No versions yet
            "created_at": datetime.now().isoformat()
        }
        self.data["active_protocol_id"] = protocol_id
        self._save_history()
        
        print(f"\n    [VERSION CONTROL] Created new protocol track: #{protocol_id} '{name}'")
        return protocol_id
    
    def set_active_protocol(self, protocol_id: str) -> bool:
        """Set which protocol is currently active"""
        if protocol_id not in self.data["protocols"]:
            print(f"    [VERSION CONTROL] Protocol #{protocol_id} not found!")
            return False
        
        self.data["active_protocol_id"] = protocol_id
        self._save_history()
        
        proto = self.data["protocols"][protocol_id]
        print(f"\n    [VERSION CONTROL] Switched to protocol #{protocol_id}: '{proto['name']}'")
        return True
    
    def get_active_protocol_id(self) -> Optional[str]:
        """Get the currently active protocol ID"""
        return self.data.get("active_protocol_id")
    
    def get_protocol(self, protocol_id: str) -> Optional[dict]:
        """Get protocol data by ID"""
        return self.data["protocols"].get(protocol_id)
    
    def get_active_protocol(self) -> Optional[dict]:
        """Get the currently active protocol"""
        pid = self.get_active_protocol_id()
        if pid:
            return self.get_protocol(pid)
        return None
    
    def commit(self, protocol_path: Path, requirement: str, roles: list, 
               messages: list, attempts: int, protocol_id: Optional[str] = None) -> tuple:
        """
        Commit a new version to the active (or specified) protocol.
        Returns (protocol_id, version_number).
        """
        if protocol_id is None:
            protocol_id = self.get_active_protocol_id()
        
        if not protocol_id or protocol_id not in self.data["protocols"]:
            raise ValueError("No active protocol. Create one first with create_new_protocol()")
        
        proto = self.data["protocols"][protocol_id]
        version = len(proto["versions"])  # 0-indexed
        
        entry = {
            'version': version,
            'timestamp': datetime.now().isoformat(),
            'requirement': requirement,
            'protocol_file': str(protocol_path),
            'protocol_content': protocol_path.read_text(encoding='utf-8') if protocol_path.exists() else "",
            'roles': roles,
            'messages': [{'label': m['label'], 'from': m['from'], 'to': m['to']} for m in messages],
            'attempts': attempts
        }
        
        proto["versions"].append(entry)
        proto["current_version"] = version
        self._save_history()
        
        print(f"\n    [VERSION CONTROL] Protocol #{protocol_id} committed v{version}")
        return (protocol_id, version)
    
    def get_current_version(self, protocol_id: Optional[str] = None) -> Optional[dict]:
        """Get the current active version of a protocol"""
        if protocol_id is None:
            protocol_id = self.get_active_protocol_id()
        
        if not protocol_id:
            return None
            
        proto = self.get_protocol(protocol_id)
        if not proto or proto["current_version"] < 0:
            return None
        
        return proto["versions"][proto["current_version"]]
    
    def get_version(self, version_num: int, protocol_id: Optional[str] = None) -> Optional[dict]:
        """Get a specific version by number from a protocol"""
        if protocol_id is None:
            protocol_id = self.get_active_protocol_id()
        
        if not protocol_id:
            return None
            
        proto = self.get_protocol(protocol_id)
        if not proto or version_num < 0 or version_num >= len(proto["versions"]):
            return None
        
        return proto["versions"][version_num]
    
    def rollback(self, version_num: int, protocols_dir: Path, skills_generator,
                 protocol_id: Optional[str] = None) -> bool:
        """
        Rollback to a specific version within a protocol.
        Restores the protocol file and regenerates skills.
        """
        if protocol_id is None:
            protocol_id = self.get_active_protocol_id()
        
        if not protocol_id:
            print("    [VERSION CONTROL] No active protocol!")
            return False
        
        proto = self.get_protocol(protocol_id)
        if not proto:
            print(f"    [VERSION CONTROL] Protocol #{protocol_id} not found!")
            return False
        
        if version_num < 0 or version_num >= len(proto["versions"]):
            print(f"    [VERSION CONTROL] Version {version_num} not found in protocol #{protocol_id}!")
            return False
        
        target = proto["versions"][version_num]
        
        # Update current version pointer
        proto["current_version"] = version_num
        
        # Restore protocol file
        protocol_path = Path(target['protocol_file'])
        protocol_path.write_text(target['protocol_content'], encoding='utf-8')
        
        # Extract module name from file path
        module_name = protocol_path.stem
        
        # Regenerate skills files
        skills_generator.generate_all_skills(
            target['protocol_content'], 
            module_name
        )
        
        self._save_history()
        
        print(f"\n    [VERSION CONTROL] Protocol #{protocol_id} rolled back to v{version_num}")
        return True
    
    def list_protocols(self) -> str:
        """List all protocols"""
        if not self.data["protocols"]:
            return "No protocols created yet."
        
        lines = ["\n" + "="*70]
        lines.append("PROTOCOLS")
        lines.append("="*70)
        lines.append(f"{'#':<4} {'Name':<25} {'Versions':<10} {'Description':<30}")
        lines.append("-"*70)
        
        active_id = self.get_active_protocol_id()
        
        for pid, proto in sorted(self.data["protocols"].items(), key=lambda x: int(x[0])):
            active = " *" if pid == active_id else ""
            name = proto['name'][:22] + "..." if len(proto['name']) > 25 else proto['name']
            versions = f"v0-v{len(proto['versions'])-1}" if proto['versions'] else "none"
            desc = proto['description'][:27] + "..." if len(proto['description']) > 30 else proto['description']
            lines.append(f"{pid:<4}{active} {name:<25} {versions:<10} {desc}")
        
        lines.append("-"*70)
        lines.append("* = active protocol")
        lines.append("="*70)
        
        return "\n".join(lines)
    
    def list_versions(self, protocol_id: Optional[str] = None) -> str:
        """List all versions of a protocol"""
        if protocol_id is None:
            protocol_id = self.get_active_protocol_id()
        
        if not protocol_id:
            return "No active protocol. Use 'protocols' to see available protocols."
        
        proto = self.get_protocol(protocol_id)
        if not proto:
            return f"Protocol #{protocol_id} not found."
        
        if not proto["versions"]:
            return f"Protocol #{protocol_id} '{proto['name']}' has no versions yet."
        
        lines = ["\n" + "="*70]
        lines.append(f"PROTOCOL #{protocol_id}: {proto['name']}")
        lines.append("="*70)
        lines.append(f"{'Ver':<5} {'Timestamp':<20} {'Roles':<8} {'Requirement':<35}")
        lines.append("-"*70)
        
        current_v = proto["current_version"]
        
        for v in proto["versions"]:
            current = " *" if v['version'] == current_v else ""
            timestamp = v['timestamp'][:19]
            roles = str(len(v.get('roles', [])))
            req = v['requirement'][:32] + "..." if len(v['requirement']) > 35 else v['requirement']
            lines.append(f"v{v['version']:<4}{current} {timestamp:<20} {roles:<8} {req}")
        
        lines.append("-"*70)
        lines.append("* = current version")
        lines.append("="*70)
        
        return "\n".join(lines)
    
    def show_version_details(self, version_num: int, protocol_id: Optional[str] = None) -> str:
        """Show detailed info about a specific version"""
        if protocol_id is None:
            protocol_id = self.get_active_protocol_id()
        
        if not protocol_id:
            return "No active protocol."
        
        v = self.get_version(version_num, protocol_id)
        if not v:
            return f"Version {version_num} not found in protocol #{protocol_id}."
        
        proto = self.get_protocol(protocol_id)
        
        lines = ["\n" + "="*70]
        lines.append(f"PROTOCOL #{protocol_id} '{proto['name']}' - VERSION {v['version']}")
        lines.append("="*70)
        lines.append(f"Timestamp:   {v['timestamp']}")
        lines.append(f"Requirement: {v['requirement']}")
        lines.append(f"Attempts:    {v['attempts']}")
        lines.append(f"Roles:       {', '.join(v.get('roles', []))}")
        lines.append(f"Protocol:    {v['protocol_file']}")
        lines.append("-"*70)
        lines.append("MESSAGES:")
        for m in v.get('messages', []):
            lines.append(f"  {m['label']}() from {m['from']} to {m['to']}")
        lines.append("-"*70)
        lines.append("PROTOCOL CONTENT:")
        lines.append(v.get('protocol_content', 'N/A'))
        lines.append("="*70)
        
        return "\n".join(lines)
    
    def diff_versions(self, v1: int, v2: int, protocol_id: Optional[str] = None) -> str:
        """Show differences between two versions of the same protocol"""
        if protocol_id is None:
            protocol_id = self.get_active_protocol_id()
        
        if not protocol_id:
            return "No active protocol."
        
        ver1 = self.get_version(v1, protocol_id)
        ver2 = self.get_version(v2, protocol_id)
        
        if not ver1 or not ver2:
            return "One or both versions not found."
        
        proto = self.get_protocol(protocol_id)
        
        lines = ["\n" + "="*70]
        lines.append(f"DIFF: Protocol #{protocol_id} '{proto['name']}' v{v1} → v{v2}")
        lines.append("="*70)
        
        # Compare roles
        roles1 = set(ver1.get('roles', []))
        roles2 = set(ver2.get('roles', []))
        added_roles = roles2 - roles1
        removed_roles = roles1 - roles2
        
        if added_roles:
            lines.append(f"+ Added roles:   {', '.join(added_roles)}")
        if removed_roles:
            lines.append(f"- Removed roles: {', '.join(removed_roles)}")
        
        # Compare messages
        msgs1 = {(m['label'], m['from'], m['to']) for m in ver1.get('messages', [])}
        msgs2 = {(m['label'], m['from'], m['to']) for m in ver2.get('messages', [])}
        added_msgs = msgs2 - msgs1
        removed_msgs = msgs1 - msgs2
        
        if added_msgs:
            lines.append("+ Added messages:")
            for m in added_msgs:
                lines.append(f"    {m[0]}() from {m[1]} to {m[2]}")
        if removed_msgs:
            lines.append("- Removed messages:")
            for m in removed_msgs:
                lines.append(f"    {m[0]}() from {m[1]} to {m[2]}")
        
        if not added_roles and not removed_roles and not added_msgs and not removed_msgs:
            lines.append("(No structural changes)")
        
        lines.append("="*70)
        return "\n".join(lines)
