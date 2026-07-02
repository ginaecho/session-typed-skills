"""
LLM-Powered Skills File Generator

Uses Claude to generate comprehensive skills.md files for each role.
"""

import re
from pathlib import Path
from stjp_core.foundry.llm_client import LLMClient
from stjp_core.authoring.prompts import SKILLS_SYSTEM_PROMPT, get_skills_generation_prompt


class SkillsGenerator:
    """
    Generates skills.md files for each role using LLM.
    
    The LLM analyzes the protocol AND the user requirement to create detailed instructions
    that another LLM agent can follow to correctly participate in the protocol.
    
    IMPORTANT: The skills files capture BUSINESS RULES (thresholds, conditions)
    that cannot be expressed in the Scribble protocol itself.
    """
    
    def __init__(self, skills_dir: Path):
        self.skills_dir = skills_dir
        self.skills_dir.mkdir(parents=True, exist_ok=True)
        self.llm = LLMClient()
        
    def generate_all_skills(self, protocol_content: str, protocol_name: str, 
                            user_requirement: str = ""):
        """
        Generate skills files for all roles in the protocol.
        
        Args:
            protocol_content: The Scribble protocol code
            protocol_name: The protocol/module name
            user_requirement: The original user requirement (contains business rules!)
        """
        
        # Parse roles from protocol
        roles = self._extract_roles(protocol_content)
        
        # Identify which roles have choices (they need decision rules)
        choice_roles = self._extract_choice_roles(protocol_content)
        
        print(f"\n    [SKILLS-LLM] Generating skills for {len(roles)} roles...")
        if choice_roles:
            print(f"    [SKILLS-LLM] Roles with decision logic: {', '.join(choice_roles)}")
        if user_requirement:
            print(f"    [SKILLS-LLM] Will extract business rules from requirement")
        
        for role in roles:
            self._generate_role_skills_llm(role, protocol_content, protocol_name, user_requirement)
            
        print(f"    [SKILLS-LLM] All skills files created in {self.skills_dir}")
        
    def _extract_roles(self, content: str) -> list[str]:
        """Extract role names from protocol"""
        match = re.search(r'global protocol \w+\((.*?)\)', content, re.DOTALL)
        if match:
            return re.findall(r'role (\w+)', match.group(1))
        return []
    
    def _extract_choice_roles(self, content: str) -> list[str]:
        """Extract roles that have 'choice at' (decision makers)"""
        return re.findall(r'choice at (\w+)', content)
    
    def _generate_role_skills_llm(self, role: str, protocol_content: str, 
                                   protocol_name: str, user_requirement: str = ""):
        """Generate skills.md for a specific role using LLM"""
        
        # Check if this role has decision logic
        has_choice = f"choice at {role}" in protocol_content
        
        if has_choice:
            print(f"      - Generating {role}_skills.md (DECISION MAKER - will include business rules)...")
        else:
            print(f"      - Generating {role}_skills.md...")
        
        user_prompt = get_skills_generation_prompt(
            protocol_content, role, protocol_name, user_requirement
        )
        
        # Call LLM to generate skills
        skills_content = self.llm.generate(SKILLS_SYSTEM_PROMPT, user_prompt)
        
        # Clean up the response (remove any markdown code blocks if present)
        if skills_content.startswith("```"):
            skills_content = re.sub(r'^```\w*\n', '', skills_content)
            skills_content = re.sub(r'\n```$', '', skills_content)
        
        # Write the file
        skills_file = self.skills_dir / f"{role}_skills.md"
        skills_file.write_text(skills_content, encoding='utf-8')
        print(f"        Created: {role}_skills.md")
