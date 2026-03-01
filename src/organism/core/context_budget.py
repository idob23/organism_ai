"""ContextBudget: controls total prompt size sent to LLM.

Keeps prompts in the ~3000 token sweet spot by trimming context sections
according to priority. Task and system are protected; lower-priority sections
(previous_steps → memory_context → knowledge_rules) are trimmed first.

Token estimation: 1 token ≈ 4 chars (rough but fast approximation).
"""


class ContextBudget:
    """Build trimmed (system_prompt, user_prompt) that fit within budget_tokens."""

    # Default allocation per section (tokens)
    ALLOC_SYSTEM  = 800
    ALLOC_RULES   = 300
    ALLOC_MEMORY  = 500
    ALLOC_TASK    = 400
    ALLOC_STEPS   = 700
    ALLOC_RESERVE = 300
    # Sum = 3000

    def __init__(self, budget_tokens: int = 3000) -> None:
        self.budget_tokens = budget_tokens
        self.last_usage: dict[str, int] = {}   # populated by build_prompt; use for logging

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def est(text: str) -> int:
        """Estimate token count: 1 token ≈ 4 chars."""
        return len(text) // 4

    @staticmethod
    def chars(tokens: int) -> int:
        """Convert token budget back to char limit."""
        return tokens * 4

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def build_prompt(
        self,
        system: str,
        knowledge_rules: list[str],
        memory_context: str,
        task: str,
        previous_steps: str = "",
    ) -> tuple[str, str]:
        """Return (system_prompt, user_prompt) trimmed to fit budget_tokens.

        Priority allocation (tokens):
          system 800 | rules 300 | memory 500 | task 400 | steps 700 | reserve 300

        Redistribution: if a section is under its allocation, the surplus is
        naturally available to lower-priority sections that come later in the
        allocation order. A second pass redistributes any leftover to sections
        that were capped at their allocation but still had more content.

        Trim order when over budget (lowest priority first):
          previous_steps → memory_context → knowledge_rules
          (task is always fully included; system is trimmed to ALLOC_SYSTEM)
        """
        C = self.chars
        E = self.est

        # --- System: hard cap at allocation ---
        system_out = system[:C(self.ALLOC_SYSTEM)]
        sys_tok = E(system_out)

        # --- Task: always fully included (protected) ---
        task_tok = E(task)

        # --- Budget available for variable context sections ---
        context_budget = self.budget_tokens - sys_tok - task_tok - self.ALLOC_RESERVE
        context_budget = max(context_budget, 0)

        # --- Raw content for variable sections ---
        rules_raw = "\n".join(f"- {r}" for r in knowledge_rules) if knowledge_rules else ""
        rules_tok = E(rules_raw)
        mem_tok   = E(memory_context)
        steps_tok = E(previous_steps)
        total_needed = rules_tok + mem_tok + steps_tok

        if total_needed <= context_budget:
            # Everything fits — no trimming needed
            rules_out  = rules_raw
            memory_out = memory_context
            steps_out  = previous_steps
        else:
            # Trim lowest priority first: steps → memory → rules
            # Allocate to high-priority sections first so they are protected.
            remaining = context_budget

            rules_out  = rules_raw[     :C(min(rules_tok, self.ALLOC_RULES,  remaining))]
            remaining -= E(rules_out)

            memory_out = memory_context[ :C(min(mem_tok,   self.ALLOC_MEMORY, remaining))]
            remaining -= E(memory_out)

            steps_out  = previous_steps[ :C(min(steps_tok, max(0, remaining)))]
            remaining -= E(steps_out)

            # Second pass: redistribute leftover to lower-priority sections
            # that were capped at their allocation but still have more content.
            if remaining > 0 and mem_tok > E(memory_out):
                extra = min(remaining, mem_tok - E(memory_out))
                memory_out = memory_context[:C(E(memory_out) + extra)]
                remaining -= extra

            if remaining > 0 and steps_tok > E(steps_out):
                extra = min(remaining, steps_tok - E(steps_out))
                steps_out = previous_steps[:C(E(steps_out) + extra)]

        # --- Track usage for caller logging ---
        self.last_usage = {
            "system": sys_tok,
            "rules":  E(rules_out),
            "memory": E(memory_out),
            "task":   task_tok,
            "steps":  E(steps_out),
            "total":  sys_tok + E(rules_out) + E(memory_out) + task_tok + E(steps_out),
        }

        # --- Build user prompt ---
        user_parts = [task]
        if rules_out:
            user_parts.append(f"[Rules:\n{rules_out}]")
        if memory_out:
            user_parts.append(f"[Memory:\n{memory_out}]")
        if steps_out:
            user_parts.append(f"[Previous steps:\n{steps_out}]")

        return system_out, "\n\n".join(user_parts)
