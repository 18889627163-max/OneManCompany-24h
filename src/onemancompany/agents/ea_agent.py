"""EA Agent — Executive Assistant that classifies and routes all CEO tasks.

ALL CEO tasks come to the EA first. The EA analyzes the task, determines
the best agent to handle it, and dispatches using dispatch_child().
"""

from __future__ import annotations

from langchain_core.messages import HumanMessage, SystemMessage

from onemancompany.agents.base import BaseAgentRunner, create_runtime_react_agent as create_react_agent, extract_final_content, make_llm
from onemancompany.core.config import CEO_ID, COO_ID, CSO_ID, EA_ID, HR_ID, MAX_SUMMARY_LEN, STATUS_IDLE, STATUS_WORKING


# EA operational prompt is now in employees/00004/role_guide.md (loaded by _get_role_identity_section)


class EAAgent(BaseAgentRunner):
    role = "EA"
    employee_id = EA_ID

    def __init__(self) -> None:
        from onemancompany.core.tool_registry import tool_registry

        self._agent_tools = tool_registry.get_proxied_tools_for(self.employee_id)
        self._agent = create_react_agent(
            model=make_llm(self.employee_id),
            tools=self._agent_tools,
        )

    def _get_role_identity_section(self) -> str:
        from onemancompany.core.config import EMPLOYEES_DIR, read_text_utf
        guide_path = EMPLOYEES_DIR / self.employee_id / "role_guide.md"
        if guide_path.exists():
            return read_text_utf(guide_path)
        return ""

    def _get_tools_prompt_section(self) -> str:
        """Keep an explicit authorization boundary even before tools are registered."""
        section = super()._get_tools_prompt_section()
        if section:
            return section
        return "\n\n## Your Authorized Tools:\nNo tools are currently registered for this runtime."

    def _customize_prompt(self, pb) -> None:
        pass  # All EA prompt content is in role_guide.md

    async def run(self, task: str) -> str:
        self._set_status(STATUS_WORKING)
        await self._publish("agent_thinking", {"message": f"EA analyzing: {task}"})

        prompt = await self._inject_long_term_memory(self._build_full_prompt(), task)
        result = await self._agent.ainvoke(
            self._checkpoint_input(prompt, task),
            config=__import__("onemancompany.core.runtime_context", fromlist=["langgraph_invoke_config"]).langgraph_invoke_config(recursion_limit=80),
        )

        self._extract_and_record_usage(result)
        final = extract_final_content(result)
        self._set_status(STATUS_IDLE)
        await self._publish("agent_done", {"role": "EA", "summary": final[:MAX_SUMMARY_LEN]})
        return final
