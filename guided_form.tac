--[[
Guided conversation demo: programmatic system prompt + CLI HITL.

Collects three fields (name, email, goal) via chat. Procedure updates State so each
agent turn re-renders {state.still_needed} and {state.collected_summary} in the system prompt.

Run (from this directory, with API key in env or .tactus/config.yml):
  tactus run guided_form.tac

Mock spec (no network):
  tactus test guided_form.tac --mock --param skip_hitl=true
--]]

local done = require("tactus.tools.done")

local FIELD_ORDER = {"name", "email", "goal"}
local FIELD_LABEL = {
    name = "Full name (given + family name)",
    email = "Email address",
    goal = "Primary goal for this session",
}

-- At least two whitespace-separated words (given + family name).
local function is_full_name(s)
    if type(s) ~= "string" then
        return false
    end
    local t = string.gsub(s, "^%s*(.-)%s*$", "%1")
    local n = 0
    for part in string.gmatch(t, "%S+") do
        n = n + 1
        if n >= 2 then
            return true
        end
    end
    return false
end

local function field_state_key(field)
    return "form_" .. field
end

local function refresh_summaries()
    local missing = {}
    local collected_lines = {}
    for _, f in ipairs(FIELD_ORDER) do
        local key = field_state_key(f)
        local v = state[key]
        if v == nil or v == "" then
            table.insert(missing, FIELD_LABEL[f])
        else
            table.insert(collected_lines, FIELD_LABEL[f] .. ": " .. tostring(v))
        end
    end
    if #missing == 0 then
        state.still_needed = "(none — all fields collected)"
    else
        state.still_needed = table.concat(missing, "; ")
    end
    if #collected_lines == 0 then
        state.collected_summary = "(nothing yet)"
    else
        state.collected_summary = table.concat(collected_lines, " | ")
    end
end

local function all_fields_set()
    for _, f in ipairs(FIELD_ORDER) do
        local v = state[field_state_key(f)]
        if v == nil or v == "" then
            return false
        end
    end
    return true
end

local function truthy(v)
    if v == true then
        return true
    end
    if v == false or v == nil then
        return false
    end
    if type(v) == "string" then
        return string.lower(v) == "true" or v == "1"
    end
    if type(v) == "number" then
        return v ~= 0
    end
    return false
end

guide = Agent {
    name = "guide",
    provider = "openai",
    model = "gpt-5.4-mini",
    -- Required: every assistant turn must call exactly one tool (see chat_only / record_field / done).
    tool_choice = "required",
    system_prompt = [[You are a friendly intake assistant helping the user complete a short form.

State (authoritative — read every turn):
- Still to collect: {state.still_needed}
- Already collected: {state.collected_summary}

The runtime may not show a separate assistant message when you use tools — your user-facing words MUST go in the tool arguments (see below).

You MUST call exactly one tool on every turn:

1) record_field — when the user provides (or clearly implies) a **full name** (given + family name: at least two words), email, or goal. For names: do not record a single word like “Ryan” as the full name — use chat_only and ask for first and last name. Put your short acknowledgment and next-step question in `note_to_user`.
2) chat_only — when they did NOT provide new form data (small talk, off-topic asks, “can you hear me?”, confusion). You MUST still write a full `reply`: briefly answer or acknowledge what they asked (e.g. if they want a random number, give one), then steer back to the next missing field. Never ignore the user’s words.
3) done — only when still_needed shows **(none — all fields collected)**. If goal is still missing, you have not finished; do not call done.

Rules:
- For **goal**, accept short informal phrases (e.g. curiosity, testing the assistant, a hobby topic like rabbits, “see what you’d say”). If they describe what they want in ordinary language, that counts — call record_field(goal) with a brief paraphrase; do not insist on a formal “business objective.”
- Never say you saved something unless you called record_field for it in this or a prior turn.
- If they mix small talk with a name, email, or goal, call record_field for the data and use `note_to_user` for the rest.
- Do not repeat the same question verbatim; vary wording if you must ask again.
- Keep replies short (2–4 sentences) unless the user asks for detail.]],

    inline_tools = {
        {
            name = "chat_only",
            description = "Use when there is no new name, email, or goal to record. You MUST still speak to the user: put the full visible message in `reply` (answer tangents briefly, then ask for the next missing field).",
            input = {
                reason = field.string{
                    required = false,
                    description = "Short internal note (optional)",
                },
                reply = field.string{
                    required = true,
                    description = "Exact message the user should read (2–4 sentences). Do not leave empty.",
                },
            },
            handler = function(args)
                state.last_user_message = tostring(args.reply or "")
                return {ok = true, skipped = "no new form data"}
            end,
        },
        {
            name = "record_field",
            description = "Record one form field. field must be name, email, or goal. For name, value must be a full name (at least two words).",
            input = {
                field = field.string{
                    required = true,
                    description = "One of: name, email, goal",
                },
                value = field.string{
                    required = true,
                    description = "For field=name: full given + family name (two+ words). For email/goal: the value to store.",
                },
                note_to_user = field.string{
                    required = true,
                    description = "What the user should see: confirm what you saved and what you still need next.",
                },
            },
            handler = function(args)
                local f = string.lower(string.gsub(args.field or "", "^%s*(.-)%s*$", "%1"))
                local allowed = {name = true, email = true, goal = true}
                if not allowed[f] then
                    return {ok = false, error = "field must be name, email, or goal"}
                end
                local val = args.value
                if val == nil or val == "" then
                    return {ok = false, error = "value is required"}
                end
                if f == "name" and not is_full_name(val) then
                    return {
                        ok = false,
                        error = "Full name must include at least two words (given and family name). Ask the user for both.",
                    }
                end
                state[field_state_key(f)] = val
                refresh_summaries()
                state.last_user_message = tostring(args.note_to_user or "")
                return {ok = true, field = f, still_needed = state.still_needed}
            end,
        },
    },
    tools = {done},
}

Procedure {
    input = {
        kickoff = field.string{
            default = "Hello — I'd like to complete the intake form. Please walk me through it.",
            description = "First user message to the agent",
        },
        skip_hitl = field.boolean{
            default = false,
            description = "Use mock_user_replies instead of Human.input (for tests / automation)",
        },
        mock_user_replies = field.array{
            required = false,
            description = "When skip_hitl is true, lines used as user messages after each assistant turn",
        },
        max_turns = field.number{default = 24, description = "Safety limit on chat turns"},
    },
    output = {
        completed = field.boolean{required = true, description = "Whether all required fields were collected"},
        turns = field.number{required = true, description = "Agent turns executed"},
        name = field.string{required = false},
        email = field.string{required = false},
        goal = field.string{required = false},
    },
    function(input)
        state.form_name = nil
        state.form_email = nil
        state.form_goal = nil
        -- String transcript (reliable across Lua/Python state bridge; arrays can round-trip badly).
        state._assistant_transcript = ""
        refresh_summaries()

        local reply_queue = {}
        if truthy(input.skip_hitl) and input.mock_user_replies then
            for _, line in ipairs(input.mock_user_replies) do
                table.insert(reply_queue, line)
            end
        end

        local user_msg = input.kickoff or "Hello."
        local max_turns = input.max_turns or 24
        local turns = 0
        local round = 0

        -- Form state for the human mirrors state.still_needed / state.collected_summary in the
        -- agent system_prompt and in wrap_user_message; printed each turn so it stays in sync.

        local function print_form_state_for_user()
            print("[Form state]")
            print("  Still to collect: " .. tostring(state.still_needed))
            print("  Already collected: " .. tostring(state.collected_summary))
        end

        local function wrap_user_message(raw)
            refresh_summaries()
            return "[Form state — still_needed: "
                .. tostring(state.still_needed)
                .. " | collected: "
                .. tostring(state.collected_summary)
                .. "]\n\nUser message:\n"
                .. tostring(raw)
        end

        -- One-line summary for the CLI transcript (full text still sent to the agent above).
        local function user_message_for_display(raw)
            local s = tostring(raw or "")
            s = string.gsub(s, "^%s*(.-)%s*$", "%1")
            if s == "" then
                return "(empty message)"
            end
            if #s <= 96 then
                return s
            end
            local limit = math.min(#s, 160)
            for i = 12, limit do
                local c = string.sub(s, i, i)
                if c == "." or c == "!" or c == "?" then
                    return string.sub(s, 1, i) .. " …"
                end
            end
            return string.sub(s, 1, 93) .. "…"
        end

        -- optional_user_echo: show as [User] before [Assistant] (summary line; model still gets full text in msg).
        local function run_guide(call_label, msg, optional_user_echo)
            turns = turns + 1
            state.last_user_message = nil
            guide({message = msg})
            local text = ""
            if state.last_user_message and #tostring(state.last_user_message) > 0 then
                text = tostring(state.last_user_message)
            elseif guide.output then
                text = tostring(guide.output)
            end
            -- Avoid showing raw UsageStats / None when tools swallowed the LM string.
            if
                text == ""
                or text == "None"
                or string.find(text, "UsageStats", 1, true)
                or string.find(text, "prompt_tokens", 1, true)
            then
                text = "(Assistant produced no user-visible text; check tool args — reply/note_to_user should be set.)"
            end
            state._assistant_transcript = (state._assistant_transcript or "")
                .. "\n[TURN]"
                .. tostring(call_label)
                .. "\n"
                .. tostring(text)
                .. "\n"
            if optional_user_echo ~= nil then
                print("[User]\n" .. user_message_for_display(optional_user_echo))
                print("")
            end
            print("[Assistant" .. call_label .. "]\n" .. text)
        end

        repeat
            round = round + 1
            if round > max_turns then
                print("Stopped: max_turns (conversation rounds) reached.")
                break
            end

            refresh_summaries()
            -- After the user’s reply at ›, separate the next round with a blank line + rule.
            if round > 1 then
                print("")
                print(string.rep("─", 48))
                print("")
            end
            print_form_state_for_user()

            run_guide("", wrap_user_message(user_msg), user_msg)

            -- The model may call done before every field is stored; do not end the procedure on done alone.
            if done.called() and not all_fields_set() then
                print(
                    "[Procedure] Completion was signaled before all fields were recorded; nudging the agent.\n"
                )
                run_guide(
                    " · completion blocked",
                    wrap_user_message(
                        "SYSTEM: You called the done tool, but still_needed is not empty. "
                            .. "Do not call done until still_needed shows (none — all fields collected). "
                            .. "If the user already stated a goal (even informally: curiosity, testing, a topic like rabbits, or what they want from this chat), call record_field with field=goal and a short value. "
                            .. "Otherwise ask one concise question for the missing field."
                    ),
                    nil
                )
            end

            -- If the model recorded all fields but forgot to call done, give one explicit turn.
            if all_fields_set() and not done.called() then
                run_guide(
                    " · completion nudge",
                    wrap_user_message(
                        "SYSTEM: All three fields are already stored. Call the done tool now with a one-line reason. Do not ask the user for more information."
                    ),
                    nil
                )
            end

            -- Reliable exit: orchestration guarantees completion once data is in state.
            if all_fields_set() and not done.called() then
                done({reason = "All required fields recorded."})
                print("[Procedure] Recorded completion via done (agent did not call it).\n")
            end

            if all_fields_set() then
                break
            end

            if truthy(input.skip_hitl) then
                user_msg = table.remove(reply_queue, 1)
                if user_msg == nil then
                    print("Stopped: no more mock_user_replies.")
                    break
                end
            else
                -- CLI prints the agent above; leave Human.input message empty so we do not duplicate a fake "Assistant" box.
                user_msg = Human.input({message = "", placeholder = ""})
            end
        until false

        refresh_summaries()
        return {
            completed = all_fields_set(),
            turns = turns,
            name = state.form_name,
            email = state.form_email,
            goal = state.form_goal,
        }
    end,
}

Specification([[
Feature: Guided form intake
  A procedure-driven intake chat where Lua updates `state.form_name`, `state.form_email`, and
  `state.form_goal`, which flow into `{state.still_needed}` and `{state.collected_summary}` in the
  agent system prompt. The guided agent must call tools every turn (`chat_only`, `record_field`, or
  `done`). Users often answer out of order: they may vent, joke, or state a purpose (e.g. rabbits,
  “talking with you”) before giving a full name or email. The agent is expected to infer and record
  informal goals, steer back to missing slots, and vary follow-up questions. Orchestration ignores a
  premature `done` when fields remain, nudges the model to call `done` after all fields are stored,
  and may call `done` from the procedure if the model still omits it.

  Scenario: Happy path — name, email, goal in order (mocked)
    Given the procedure has started
    And the input skip_hitl is "true"
    And the agent "guide" responds with "Recorded."
    And the agent "guide" calls tool "guide_record_field" with args {"field": "name", "value": "Pat Example", "note_to_user": "Recorded your name."}
    And the agent "guide" calls tool "guide_record_field" with args {"field": "email", "value": "pat@example.com", "note_to_user": "Got it."}
    And the agent "guide" calls tool "guide_record_field" with args {"field": "goal", "value": "Ship the demo", "note_to_user": "Thanks."}
    And the agent "guide" calls tool "done" with args {"reason": "Intake complete"}
    When the procedure runs
    Then the done tool should be called
    And the procedure should complete successfully

  Scenario: Out-of-order recording — goal, then name, then email (mocked, single assistant turn)
    The checklist order in code is name → email → goal, but `record_field` may be called in any
    order. This mirrors a user who states purpose before contact details (as in the README
    “rabbits / George” CLI example); multi-turn behavior is covered there and in pytest integration.
    Given the procedure has started
    And the input skip_hitl is "true"
    And the agent "guide" responds with "Recorded out of order."
    And the agent "guide" calls tool "guide_record_field" with args {"field": "goal", "value": "Ship the guided demo", "note_to_user": "Got your goal first."}
    And the agent "guide" calls tool "guide_record_field" with args {"field": "name", "value": "Pat Example", "note_to_user": "Name saved."}
    And the agent "guide" calls tool "guide_record_field" with args {"field": "email", "value": "pat@example.com", "note_to_user": "Email saved."}
    And the agent "guide" calls tool "done" with args {"reason": "Intake complete"}
    When the procedure runs
    Then the done tool should be called
    And the procedure should complete successfully
]])
