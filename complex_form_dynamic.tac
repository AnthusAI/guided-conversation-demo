--[[
Complex form intake — dynamic arm: same BASE_SYSTEM_PROMPT as static, plus per-turn
ephemeral orchestrator hint via guide({ system_prompt_suffix = ... }) (appended to BASE).

Run: tactus run complex_form_dynamic.tac
Mock: tactus test complex_form_dynamic.tac --mock --param skip_hitl=true
--]]

local done = require("tactus.tools.done")

local ALWAYS_REQUIRED = {
    "first_name", "last_name", "email", "phone",
    "service_type", "street_address", "zip_code",
    "preferred_date", "session_goal",
}
local COMMERCIAL_ONLY = {"company_name", "tax_id"}

local FIELD_LABEL = {
    first_name     = "First name",
    last_name      = "Last name",
    email          = "Email address",
    phone          = "Phone number (XXX-XXX-XXXX)",
    service_type   = "Service type (Residential or Commercial)",
    street_address = "Street address (house number + street name)",
    zip_code       = "ZIP code (5 digits)",
    preferred_date = "Preferred service date (YYYY-MM-DD)",
    session_goal   = "Reason for the service request",
    company_name   = "Company name",
    tax_id         = "Business tax ID (XX-XXXXXXX)",
}

local function get_required_fields()
    local fields = {}
    for _, f in ipairs(ALWAYS_REQUIRED) do
        table.insert(fields, f)
    end
    if state.form_service_type == "Commercial" then
        for _, f in ipairs(COMMERCIAL_ONLY) do
            table.insert(fields, f)
        end
    end
    return fields
end

local function valid_email(s)
    if type(s) ~= "string" then
        return false
    end
    local at = s:find("@")
    if not at or at <= 1 then
        return false
    end
    local dot = s:find("%.", at + 1)
    return dot ~= nil and dot < #s
end

local VALIDATORS = {
    first_name     = function(s) return type(s)=="string" and #s>0 and not s:find("%d") end,
    last_name      = function(s) return type(s)=="string" and #s>0 and not s:find("%d") end,
    email          = valid_email,
    phone          = function(s) return type(s)=="string" and s:match("^%d%d%d%-%d%d%d%-%d%d%d%d$")~=nil end,
    service_type   = function(s) return s=="Residential" or s=="Commercial" end,
    street_address = function(s) return type(s)=="string" and #s>5 and s:find("%d")~=nil and s:find(" ")~=nil end,
    zip_code       = function(s) return type(s)=="string" and s:match("^%d%d%d%d%d$")~=nil end,
    preferred_date = function(s) return type(s)=="string" and s:match("^%d%d%d%d%-%d%d%-%d%d$")~=nil end,
    session_goal   = function(s) return type(s)=="string" and #s>=5 end,
    company_name   = function(s) return type(s)=="string" and #s>0 end,
    tax_id         = function(s) return type(s)=="string" and s:match("^%d%d%-%d%d%d%d%d%d%d$")~=nil end,
}

local VALIDATOR_ERRORS = {
    first_name     = "First name must contain letters only (no digits).",
    last_name      = "Last name must contain letters only (no digits).",
    email          = "Email must be in the format user@domain.ext.",
    phone          = "Phone must be formatted exactly as XXX-XXX-XXXX (e.g. 555-123-4567).",
    service_type   = "Service type must be exactly 'Residential' or 'Commercial'.",
    street_address = "Street address must include a house number and street name.",
    zip_code       = "ZIP code must be exactly 5 digits.",
    preferred_date = "Date must be formatted exactly as YYYY-MM-DD (e.g. 2026-05-15).",
    session_goal   = "Please provide a brief description of why you are calling (at least 5 characters).",
    company_name   = "Company name cannot be empty.",
    tax_id         = "Business tax ID must be formatted exactly as XX-XXXXXXX (e.g. 12-3456789).",
}

local function refresh_summaries()
    local required = get_required_fields()
    local missing, collected = {}, {}
    for _, f in ipairs(required) do
        local v = state["form_" .. f]
        if v == nil or v == "" then
            table.insert(missing, FIELD_LABEL[f])
        else
            table.insert(collected, FIELD_LABEL[f] .. ": " .. tostring(v))
        end
    end
    if #missing == 0 then
        state.still_needed = "(none — all fields collected)"
    else
        state.still_needed = table.concat(missing, "; ")
    end
    state.collected_summary = #collected == 0 and "(nothing yet)" or table.concat(collected, " | ")
end

local function all_fields_set()
    for _, f in ipairs(get_required_fields()) do
        local v = state["form_" .. f]
        if v == nil or v == "" then return false end
    end
    return true
end

-- Same base text as complex_form_static.tac (A/B differs only by per-turn orchestrator hint below).
local BASE_SYSTEM_PROMPT = [[You are a friendly service intake assistant.

You must collect ALL of the following information before calling done.

ALWAYS-REQUIRED FIELDS:
1. first_name — letters only, no digits
2. last_name — letters only, no digits
3. email — must contain @ and a valid domain (e.g. user@example.com)
4. phone — must be formatted EXACTLY as XXX-XXX-XXXX (e.g. 555-123-4567)
5. service_type — must be EXACTLY "Residential" or "Commercial"
6. street_address — must include a house or building number and street name
7. zip_code — must be EXACTLY 5 digits
8. preferred_date — must be formatted EXACTLY as YYYY-MM-DD (e.g. 2026-05-15)
9. session_goal — a brief reason for the service call (at least a few words)

CONDITIONAL FIELDS (only required when service_type is "Commercial"):
10. company_name — name of the business
11. tax_id — business tax ID, formatted EXACTLY as XX-XXXXXXX (e.g. 12-3456789)

If service_type is "Residential", fields 10 and 11 are NOT required.

You MUST call exactly one tool on every turn:
1) record_field — use this immediately when the user provides a value for any field.
   - Normalize phone to XXX-XXX-XXXX before recording.
   - Normalize date to YYYY-MM-DD before recording.
   - Normalize tax_id to XX-XXXXXXX before recording.
   - Record service_type as exactly "Residential" or "Commercial".
   - If a value fails validation, report the error and ask for a corrected value.
2) chat_only — use this when no new field data was provided. MUST include a reply asking for the next missing field.
3) done — ONLY when ALL required fields (including company_name and tax_id if Commercial) have been recorded.

Rules:
- Never say you saved something unless you actually called record_field for it.
- Do not repeat the same question verbatim — vary phrasing on follow-ups.
- Keep replies to 2–4 sentences.]]

local function wrap_user_message(raw)
    return tostring(raw)
end

-- Ephemeral orchestrator hint: appended to the agent's BASE via guide({ system_prompt_suffix = ... }) (not chat history).
-- Sanitize values so Tactus TemplateResolver does not treat user text like {state.foo} as template markers.
local function sanitize_for_system_template(s)
    s = tostring(s or "")
    return (s:gsub("%{", "("):gsub("%}", ")"))
end

local function orchestrator_suffix_for_turn()
    refresh_summaries()
    local miss = sanitize_for_system_template(state.still_needed)
    local coll = sanitize_for_system_template(state.collected_summary)
    return "--- Orchestrator hint (ephemeral — applies only to this model call; not a separate chat message) ---\n"
        .. "Still to collect: "
        .. miss
        .. "\nAlready collected: "
        .. coll
        .. "\nUse the above lines to decide what to ask next. When Still to collect shows (none — all fields collected), you may finish with done."
end

guide = Agent {
    name = "guide",
    provider = "openai",
    model = "gpt-5.4-mini",
    tool_choice = "required",
    system_prompt = BASE_SYSTEM_PROMPT,

    inline_tools = {
        {
            name = "chat_only",
            description = "Use when there is no new form data to record. Put the full visible message in reply.",
            input = {
                reason = field.string{required = false, description = "Internal note (optional)"},
                reply = field.string{required = true, description = "Message the user reads (2–4 sentences)."},
            },
            handler = function(args)
                state.last_user_message = tostring(args.reply or "")
                return {ok = true, skipped = "no new form data"}
            end,
        },
        {
            name = "record_field",
            description = "Record one intake field. field must be a known form field name.",
            input = {
                field = field.string{required = true, description = "Field name (e.g. first_name, email)."},
                value = field.string{required = true, description = "Value to store after normalization."},
                note_to_user = field.string{required = true, description = "What the user sees: confirm and next step."},
            },
            handler = function(args)
                local f = (args.field or ""):gsub("^%s*(.-)%s*$", "%1"):lower()
                local all_fields = {}
                for _, x in ipairs(ALWAYS_REQUIRED) do all_fields[x] = true end
                for _, x in ipairs(COMMERCIAL_ONLY) do all_fields[x] = true end
                if not all_fields[f] then
                    return {ok = false, error = "Unknown field: " .. tostring(f)}
                end
                local val = (args.value or ""):gsub("^%s*(.-)%s*$", "%1")
                if val == "" then
                    return {ok = false, error = "value is required"}
                end
                local validator = VALIDATORS[f]
                if validator and not validator(val) then
                    return {ok = false, error = VALIDATOR_ERRORS[f]}
                end
                state["form_" .. f] = val
                refresh_summaries()
                state.last_user_message = tostring(args.note_to_user or "")
                return {ok = true, field = f, still_needed = state.still_needed}
            end,
        },
    },
    tools = {done},
}

local function truthy(v)
    if v == true then return true end
    if v == false or v == nil then return false end
    if type(v) == "string" then return string.lower(v) == "true" or v == "1" end
    if type(v) == "number" then return v ~= 0 end
    return false
end

Procedure {
    input = {
        kickoff = field.string{default = "Hello, I need to schedule a service call.", description = "Opening user message"},
        skip_hitl = field.boolean{default = false, description = "Use mock_user_replies instead of Human.input"},
        mock_user_replies = field.array{required = false, description = "Scripted user lines when skip_hitl is true"},
        max_turns = field.number{default = 30, description = "Safety limit on conversation rounds"},
    },
    output = {
        completed      = field.boolean{required = true},
        turns          = field.number{required = true},
        first_name     = field.string{required = false},
        last_name      = field.string{required = false},
        email          = field.string{required = false},
        phone          = field.string{required = false},
        service_type   = field.string{required = false},
        street_address = field.string{required = false},
        zip_code       = field.string{required = false},
        preferred_date = field.string{required = false},
        session_goal   = field.string{required = false},
        company_name   = field.string{required = false},
        tax_id         = field.string{required = false},
    },
    function(input)
        for _, f in ipairs(ALWAYS_REQUIRED) do state["form_" .. f] = nil end
        for _, f in ipairs(COMMERCIAL_ONLY) do state["form_" .. f] = nil end
        state._assistant_transcript = ""
        refresh_summaries()

        local reply_queue = {}
        if truthy(input.skip_hitl) and input.mock_user_replies then
            for _, line in ipairs(input.mock_user_replies) do
                table.insert(reply_queue, line)
            end
        end

        local user_msg = input.kickoff or "Hello."
        local max_turns = input.max_turns or 30
        local turns = 0
        local round = 0

        local function print_form_state_for_user()
            print("[Form state]")
            print("  Still to collect: " .. tostring(state.still_needed))
            print("  Already collected: " .. tostring(state.collected_summary))
        end

        local function user_message_for_display(raw)
            local s = tostring(raw or "")
            s = string.gsub(s, "^%s*(.-)%s*$", "%1")
            if s == "" then return "(empty message)" end
            if #s <= 96 then return s end
            local limit = math.min(#s, 160)
            for i = 12, limit do
                local c = string.sub(s, i, i)
                if c == "." or c == "!" or c == "?" then
                    return string.sub(s, 1, i) .. " …"
                end
            end
            return string.sub(s, 1, 93) .. "…"
        end

        local function run_guide(call_label, msg, optional_user_echo)
            turns = turns + 1
            state.last_user_message = nil
            guide({message = msg, system_prompt_suffix = orchestrator_suffix_for_turn()})
            local text = ""
            if state.last_user_message and #tostring(state.last_user_message) > 0 then
                text = tostring(state.last_user_message)
            elseif guide.output then
                text = tostring(guide.output)
            end
            if text == "" or text == "None" or string.find(text, "UsageStats", 1, true)
                or string.find(text, "prompt_tokens", 1, true) then
                text = "(Assistant produced no user-visible text; check tool args — reply/note_to_user should be set.)"
            end
            state._assistant_transcript = (state._assistant_transcript or "")
                .. "\n[TURN]" .. tostring(call_label) .. "\n" .. tostring(text) .. "\n"
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
            if round > 1 then
                print("")
                print(string.rep("─", 48))
                print("")
            end
            print_form_state_for_user()

            run_guide("", wrap_user_message(user_msg), user_msg)

            if done.called() and not all_fields_set() then
                print("[Procedure] Completion signaled before all fields recorded; nudging the agent.\n")
                run_guide(
                    " · blocked",
                    wrap_user_message(
                        "SYSTEM: The form is not complete yet. Continue collecting the required information."
                    ),
                    nil
                )
            end

            if all_fields_set() and not done.called() then
                run_guide(
                    " · nudge",
                    wrap_user_message(
                        "SYSTEM: All required fields are recorded. Call the done tool now with a one-line reason. Do not ask the user for more information."
                    ),
                    nil
                )
            end

            if all_fields_set() and not done.called() then
                done({reason = "All required fields recorded."})
                print("[Procedure] Recorded completion via done (agent did not call it).\n")
            end

            if all_fields_set() then break end

            if truthy(input.skip_hitl) then
                user_msg = table.remove(reply_queue, 1)
                if user_msg == nil then
                    print("Stopped: no more mock_user_replies.")
                    break
                end
            else
                user_msg = Human.input({
                    message = state.last_user_message or "Input requested",
                    placeholder = "",
                })
            end
        until false

        refresh_summaries()
        return {
            completed       = all_fields_set(),
            turns           = turns,
            first_name      = state.form_first_name,
            last_name       = state.form_last_name,
            email           = state.form_email,
            phone           = state.form_phone,
            service_type    = state.form_service_type,
            street_address  = state.form_street_address,
            zip_code        = state.form_zip_code,
            preferred_date  = state.form_preferred_date,
            session_goal    = state.form_session_goal,
            company_name    = state.form_company_name,
            tax_id          = state.form_tax_id,
        }
    end,
}

Specification([[
Feature: Complex form intake — dynamic prompt variant
  Agent receives live still_needed / collected_summary in the system prompt and
  in wrapped user messages, updated every turn by the procedure.

  Scenario: Happy path Residential — dynamic (mocked)
    Given the procedure has started
    And the input skip_hitl is "true"
    And the agent "guide" responds with "Recorded."
    And the agent "guide" calls tool "guide_record_field" with args {"field": "first_name", "value": "Alice", "note_to_user": "Got it."}
    And the agent "guide" calls tool "guide_record_field" with args {"field": "last_name", "value": "Smith", "note_to_user": "Got it."}
    And the agent "guide" calls tool "guide_record_field" with args {"field": "email", "value": "alice@example.com", "note_to_user": "Got it."}
    And the agent "guide" calls tool "guide_record_field" with args {"field": "phone", "value": "555-019-2837", "note_to_user": "Got it."}
    And the agent "guide" calls tool "guide_record_field" with args {"field": "service_type", "value": "Residential", "note_to_user": "Got it."}
    And the agent "guide" calls tool "guide_record_field" with args {"field": "street_address", "value": "123 Oak Lane", "note_to_user": "Got it."}
    And the agent "guide" calls tool "guide_record_field" with args {"field": "zip_code", "value": "90210", "note_to_user": "Got it."}
    And the agent "guide" calls tool "guide_record_field" with args {"field": "preferred_date", "value": "2026-05-12", "note_to_user": "Got it."}
    And the agent "guide" calls tool "guide_record_field" with args {"field": "session_goal", "value": "Leaky pipe repair", "note_to_user": "Thanks."}
    And the agent "guide" calls tool "done" with args {"reason": "All fields collected"}
    When the procedure runs
    Then the done tool should be called
    And the procedure should complete successfully
]])
