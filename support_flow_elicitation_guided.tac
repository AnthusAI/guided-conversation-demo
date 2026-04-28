--[[
Support conversation flow — elicitation experiment (guided, LLM-in-the-loop).

This arm tests an MCP-elicitation-inspired control architecture: the LLM stays
in the user-facing capture loop, but every act of structured capture goes
through a single `collect_field` tool whose return shape is an in-the-moment
elicitation payload (message + JSON Schema) rather than a free-form record_field
call. The procedure programmatically validates each value, enforces field
ordering and branch constraints, and informs the LLM of next steps via the
tool's return shape. The system prompt is intentionally lean: the goal is to
measure the reliability lift from in-the-moment, tool-mediated guidance versus
a long upfront instruction blob (the unguided baseline).

Run:  tactus run support_flow_elicitation_guided.tac
Mock: tactus test support_flow_elicitation_guided.tac --mock --param skip_hitl=true
--]]

local done_tool = require("tactus.tools.done")

local ISSUE_GENERAL = "general"
local ISSUE_BILLING = "billing"
local ISSUE_TECH = "technical"

local FIELD_LABEL = {
    issue_category              = "Issue category (general, billing, or technical)",
    account_email               = "Account email on file",
    issue_summary               = "Brief issue summary",
    callback_phone              = "Callback number (XXX-XXX-XXXX)",
    device_model                = "Device or hardware model (technical path only)",
    billing_charge_acknowledged = "Acknowledgment of fee terms (yes) — billing path only",
    plan_approval               = "Customer approval of proposed plan (yes)",
}

local function valid_email(s)
    if type(s) ~= "string" then return false end
    local at = s:find("@")
    if not at or at <= 1 then return false end
    local dot = s:find("%.", at + 1)
    return dot ~= nil and dot < #s
end

local VALIDATORS = {
    issue_category = function(s)
        return s == ISSUE_GENERAL or s == ISSUE_BILLING or s == ISSUE_TECH
    end,
    account_email = valid_email,
    issue_summary = function(s) return type(s) == "string" and #s >= 5 end,
    callback_phone = function(s)
        return type(s) == "string" and s:match("^%d%d%d%-%d%d%d%-%d%d%d%d$") ~= nil
    end,
    device_model = function(s)
        if type(s) ~= "string" then return false end
        if #s < 3 then return false end
        local low = string.lower(s)
        local single_word_refusals = {
            ["yes"] = true, ["no"] = true, ["true"] = true, ["false"] = true,
            ["sure"] = true, ["ok"] = true, ["okay"] = true, ["yeah"] = true,
            ["yep"] = true, ["nope"] = true, ["pass"] = true, ["skip"] = true,
            ["none"] = true, ["null"] = true, ["nil"] = true,
        }
        if single_word_refusals[low] then return false end
        return true
    end,
    billing_charge_acknowledged = function(s) return s == "yes" end,
    plan_approval = function(s) return s == "yes" end,
}

local VALIDATOR_ERRORS = {
    issue_category              = "issue_category must be exactly general, billing, or technical.",
    account_email               = "Email must look like user@domain.ext.",
    issue_summary               = "Issue summary must be at least 5 characters.",
    callback_phone              = "Phone must be XXX-XXX-XXXX (digits and dashes only).",
    device_model                = "Device model must be the actual hardware model (3+ characters; not a yes/no/skip).",
    billing_charge_acknowledged = "Must record billing_charge_acknowledged as exactly yes after fee disclosure.",
    plan_approval               = "Must record plan_approval as exactly yes after explaining the plan.",
}

-- Elicitation-form definitions consumed by collect_field().  Each entry holds
-- the user-facing prompt text (relayed verbatim by the LLM via the
-- `[ELICITATION · FORM]` sentinel) and a flat JSON-Schema fragment compatible
-- with MCP form-mode elicitation.
local FIELD_DEFS = {
    issue_summary = {
        title  = "Issue summary",
        prompt = "Please briefly describe the problem you need help with.",
        schema = { type = "string", minLength = 5,
                   description = "A short free-text description of the customer's problem." },
    },
    issue_category = {
        title  = "Issue category",
        prompt = "Which best describes your issue? Choose one of: general, billing, or technical.",
        schema = { type = "string", enum = { "general", "billing", "technical" } },
    },
    account_email = {
        title  = "Account email",
        prompt = "Please provide the email address on the account.",
        schema = { type = "string", format = "email" },
    },
    callback_phone = {
        title  = "Callback phone",
        prompt = "Please provide a callback number in the format XXX-XXX-XXXX (digits and dashes only).",
        schema = { type = "string", pattern = "^\\d{3}-\\d{3}-\\d{4}$" },
    },
    device_model = {
        title  = "Device model",
        prompt = "Please provide the device or hardware model (example: ACME Router X200).",
        schema = { type = "string", minLength = 2 },
    },
    billing_charge_acknowledged = {
        title  = "Billing fee acknowledgment",
        prompt = "Do you acknowledge the billing fee terms? Answer with exactly: yes",
        schema = { type = "string", enum = { "yes" } },
    },
    plan_approval = {
        title  = "Plan approval",
        prompt = "Do you approve the proposed resolution plan? Answer with exactly: yes",
        schema = { type = "string", enum = { "yes" } },
    },
}

local function _ensure_trace_tables()
    state._step_trace = state._step_trace or {}
    state._violations = state._violations or {}
end

local function _trace_step(token)
    _ensure_trace_tables()
    table.insert(state._step_trace, tostring(token))
end

local function _trace_violation(action_token, reason)
    _ensure_trace_tables()
    table.insert(state._violations, {action = tostring(action_token), reason = tostring(reason or "")})
end

local function get_required_fields()
    local req = {
        "issue_summary",
        "issue_category",
        "account_email",
        "callback_phone",
        "plan_approval",
    }
    local cat = state.form_issue_category
    if cat == ISSUE_TECH then
        table.insert(req, "device_model")
    end
    if cat == ISSUE_BILLING then
        table.insert(req, "billing_charge_acknowledged")
    end
    return req
end

local function refresh_summaries()
    local req = get_required_fields()
    local missing, collected = {}, {}
    for _, f in ipairs(req) do
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

local function all_requirements_met()
    if not state.compliance_recording_done then
        return false
    end
    if state.form_issue_category == ISSUE_BILLING and not state.compliance_fee_done then
        return false
    end
    for _, f in ipairs(get_required_fields()) do
        local v = state["form_" .. f]
        if v == nil or v == "" then
            return false
        end
    end
    return true
end

local function _trim(s)
    s = tostring(s or "")
    return (s:gsub("^%s*(.-)%s*$", "%1"))
end

local function _lower(s)
    return string.lower(tostring(s or ""))
end

local function _contains(haystack, needle)
    return string.find(_lower(haystack), _lower(needle), 1, true) ~= nil
end

-- _extract_for_field is retained because the user (via the simulator) may
-- reply with a slightly noisy free-text value even when the LLM relays the
-- elicitation prompt verbatim. We normalize first, then validate.
local function _extract_for_field(field_name, raw)
    local f = _lower(_trim(field_name))
    local s = _trim(raw)
    if s == "" then return "" end

    if f == "account_email" then
        local email = s:match("([%w%._%+%-]+@[%w%.%-]+%.[%a]+)")
        if email then
            email = email:gsub("[%,%;%:]$", "")
            return email
        end
        local normalized = _lower(s)
        normalized = normalized:gsub("%s+at%s+", "@")
        normalized = normalized:gsub("%s+dot%s+", ".")
        normalized = normalized:gsub("%s+", "")
        local email2 = normalized:match("([%w%._%+%-]+@[%w%.%-]+%.[%a]+)")
        if email2 then return email2 end
        return s
    end

    if f == "callback_phone" then
        local phone = s:match("(%d%d%d%-%d%d%d%-%d%d%d%d)")
        if phone then return phone end
        local digits = s:gsub("%D", "")
        if #digits >= 10 then
            digits = digits:sub(1, 10)
            return digits:sub(1, 3) .. "-" .. digits:sub(4, 6) .. "-" .. digits:sub(7, 10)
        end
        return s
    end

    if f == "issue_category" then
        if _contains(s, ISSUE_TECH) or _contains(s, "tech") then return ISSUE_TECH end
        if _contains(s, ISSUE_BILLING) or _contains(s, "bill") then return ISSUE_BILLING end
        if _contains(s, ISSUE_GENERAL) then return ISSUE_GENERAL end
        return s
    end

    if f == "billing_charge_acknowledged" or f == "plan_approval" then
        if _contains(s, "yes") then return "yes" end
        return s
    end

    return s
end

-- Assemble the user-facing elicitation prompt the LLM is expected to relay
-- verbatim. The `[ELICITATION · FORM]` sentinel and the `(Required: <field>)`
-- line are what the simulator (tests/llm_hitl_handler.py) keys off of.
local function build_elicitation_prompt(field_name, error_msg)
    local def = FIELD_DEFS[field_name] or { title = field_name, prompt = "Please provide " .. field_name .. "." }
    local lines = {}
    table.insert(lines, "[ELICITATION · FORM] " .. def.title)
    table.insert(lines, def.prompt)
    table.insert(lines, "(Required: " .. field_name .. ")")
    if error_msg and error_msg ~= "" then
        table.insert(lines, "(Previous reply was rejected: " .. error_msg .. ")")
    end
    table.insert(lines, "Reply with just the value.")
    return table.concat(lines, "\n")
end

-- Programmatic check: is `field` allowed to be collected right now? Mirrors
-- the ordering/branching constraints record_field would enforce in unguided.
local function blocked_reason(field_name)
    local f = _lower(_trim(field_name))
    if not FIELD_DEFS[f] then
        return "Unknown field: " .. tostring(f) .. ". Allowed: " ..
            "issue_summary, issue_category, account_email, callback_phone, device_model, " ..
            "billing_charge_acknowledged, plan_approval."
    end
    if f == "account_email" and not state.compliance_recording_done then
        return "Deliver the recording/privacy disclosure (record_compliance with kind=recording_privacy) before account_email."
    end
    if f == "device_model" and state.form_issue_category ~= ISSUE_TECH then
        if state.form_issue_category == nil or state.form_issue_category == "" then
            return "Confirm issue_category first; device_model is only needed when issue_category=technical."
        end
        return "device_model is only collected on the technical branch (current category: "
            .. tostring(state.form_issue_category) .. ")."
    end
    if f == "billing_charge_acknowledged" then
        if state.form_issue_category ~= ISSUE_BILLING then
            return "billing_charge_acknowledged is only collected on the billing branch."
        end
        if not state.compliance_fee_done then
            return "Deliver the fee_terms disclosure (record_compliance with kind=fee_terms) before billing_charge_acknowledged."
        end
    end
    if f == "plan_approval" then
        if state.form_issue_category == ISSUE_TECH and (state.form_device_model or "") == "" then
            return "Collect device_model before plan_approval on the technical branch."
        end
        if state.form_issue_category == ISSUE_BILLING then
            if not state.compliance_fee_done then
                return "Deliver fee_terms disclosure before plan_approval on the billing branch."
            end
            if (state.form_billing_charge_acknowledged or "") ~= "yes" then
                return "Record billing_charge_acknowledged=yes before plan_approval on the billing branch."
            end
        end
    end
    return nil
end

-- Compute the next field we'd like the LLM to collect (informational only:
-- the LLM is free to pick any allowed field).
local function next_required_field()
    refresh_summaries()
    if not state.compliance_recording_done then
        return nil  -- next step is record_compliance, not a field
    end
    for _, f in ipairs(get_required_fields()) do
        local v = state["form_" .. f]
        if v == nil or v == "" then
            if blocked_reason(f) == nil then
                return f
            end
        end
    end
    return nil
end

-- Validate & store one value programmatically. Mirrors what
-- record_field_programmatically did in the previous (out-of-band) guided arm,
-- but is now driven by the LLM's collect_field tool call rather than by the
-- procedure's own Human.input loop.
local function validate_and_store(field_name, raw_value)
    local f = _lower(_trim(field_name))
    local normalized = _extract_for_field(f, raw_value)
    if normalized == nil or normalized == "" then
        _trace_violation("field:" .. tostring(f), "empty value")
        return false, "value is required (the user did not provide one)"
    end
    local validator = VALIDATORS[f]
    if validator and not validator(normalized) then
        _trace_violation("field:" .. tostring(f), "validation failed")
        return false, VALIDATOR_ERRORS[f]
    end
    state["form_" .. f] = normalized
    _trace_step("field:" .. tostring(f))
    refresh_summaries()
    return true, normalized
end

local GUIDE_MODEL = "gpt-5.4-mini"

-- Lean system prompt: no upfront ordering checklist, no enumeration of fields.
-- All in-the-moment guidance comes from collect_field's return shape and from
-- the SYSTEM hint the procedure prepends to each user-role turn.
local GUIDE_SYSTEM_PROMPT = [[You are a careful customer support agent.

Your job is to help the customer through the support intake. You have four tools (call exactly one per turn):

- collect_field(name [, value])
    Use this for every piece of structured data the form needs.

    ELICIT (no `value` arg): the tool returns {action="elicit", message=...}.
      Your turn ends here; the user-facing message is the `message` field. Do NOT paraphrase,
      drop, or reorder lines — relay the message exactly as returned. Wait for the user's
      next turn before doing anything else.

    SUBMIT (with `value` arg): pass exactly the user's most recent reply as `value`. The
      tool validates and returns:
        {action="accepted"}: read `next_hint` and IMMEDIATELY (same turn, same model
          response) chain the next action — usually another collect_field elicit for the
          field named in `next_hint`. Do NOT end the turn after a bare accept; the user has
          nothing to read. If everything's collected, call done() instead.
        {action="elicit"} with an `error`: validation failed. Relay the new `message`
          verbatim (it includes the error) and wait for a corrected reply.
        {action="blocked"}: complete `do_first` first (usually a record_compliance or a
          different field), then come back.

    NEVER pass a value the user did not give. NEVER assume the user said "yes" — wait for
    them to actually say it. Always pair the field name with the value the user just gave
    for THAT field.

- record_compliance(kind, note_to_user)
    Log a regulatory disclosure. `kind` is "recording_privacy" or "fee_terms".
    `note_to_user` MUST be the full disclosure text the user heard (>= 12 chars).

- chat_only(reply)
    Plain reply, no recording. Use sparingly: for greetings, plan explanation, transitions.

- done(reason)
    Finish the intake. The procedure rejects this if requirements are incomplete.

Each turn the user-role message starts with a SYSTEM line listing what is still needed
(in roughly the order the procedure expects). Trust it. Do not invent ordering rules of
your own; let the tools tell you when something is blocked.]]

guide = Agent {
    name = "guide",
    provider = "openai",
    model = GUIDE_MODEL,
    tool_choice = "required",
    system_prompt = GUIDE_SYSTEM_PROMPT,

    inline_tools = {
        {
            name = "done",
            description = "Finish the support flow when (and only when) every requirement is satisfied.",
            input = {
                reason = field.string{required = false},
            },
            handler = function(args)
                refresh_summaries()
                if not all_requirements_met() then
                    _trace_violation("done", "blocked: requirements incomplete")
                    local need = tostring(state.still_needed or "(unknown)")
                    return {
                        ok = false,
                        error = "Cannot call done yet. Still needed: " .. need,
                    }
                end
                done_tool({reason = tostring(args.reason or "Support flow complete.")})
                state.last_user_message = "Okay — I've recorded this as complete."
                return {ok = true}
            end,
        },
        {
            name = "chat_only",
            description = "Reply to the user without recording structured data. Good for the opening greeting, plan explanation, and short transitions.",
            input = {
                reply = field.string{required = true, description = "User-visible message."},
            },
            handler = function(args)
                state.last_user_message = tostring(args.reply or "")
                return {ok = true}
            end,
        },
        {
            name = "record_compliance",
            description = "After you spoke a regulatory disclosure aloud to the user, log it. note_to_user must contain the full disclosure text the user heard.",
            input = {
                kind = field.string{
                    required = true,
                    description = "recording_privacy | fee_terms",
                },
                note_to_user = field.string{
                    required = true,
                    description = "Full user-visible disclosure text (same turn).",
                },
            },
            handler = function(args)
                local kind = _lower(_trim(args.kind))
                local note = _trim(args.note_to_user)
                if #note < 12 then
                    _trace_violation("compliance:" .. kind, "note_to_user too short")
                    return {ok = false, error = "note_to_user must be at least 12 characters (the disclosure the user heard)."}
                end
                if kind == "recording_privacy" then
                    state.compliance_recording_done = true
                    _trace_step("compliance:recording_privacy")
                elseif kind == "fee_terms" then
                    if state.form_issue_category ~= ISSUE_BILLING then
                        _trace_violation("compliance:fee_terms", "fee_terms only for billing issues")
                        return {ok = false, error = "fee_terms only applies when issue_category is billing."}
                    end
                    state.compliance_fee_done = true
                    _trace_step("compliance:fee_terms")
                else
                    _trace_violation("compliance:" .. kind, "unknown compliance kind")
                    return {ok = false, error = "Unknown compliance kind. Use recording_privacy or fee_terms."}
                end
                state.last_user_message = note
                return {ok = true, kind = kind}
            end,
        },
        {
            name = "collect_field",
            description = "MCP-style elicitation tool. First call with just `name` to receive an elicitation prompt to relay to the user; second call with `name` AND `value` to submit the user's reply for validation. The tool returns one of: {action='elicit', message, requested_schema, ...}, {action='accepted', recorded, next_hint}, {action='blocked', reason, do_first}, or {action='error', reason}.",
            input = {
                name = field.string{
                    required = true,
                    description = "Field to collect. One of: issue_summary, issue_category, account_email, callback_phone, device_model, billing_charge_acknowledged, plan_approval.",
                },
                value = field.string{
                    required = false,
                    description = "User's reply, verbatim. Omit on the first call (eliciting); include on the follow-up (submitting).",
                },
            },
            handler = function(args)
                local f = _lower(_trim(args.name))
                local def = FIELD_DEFS[f]
                if def == nil then
                    _trace_violation("collect_field:" .. tostring(f), "unknown field")
                    return {
                        action = "error",
                        reason = "Unknown field: " .. tostring(f),
                    }
                end

                local block = blocked_reason(f)
                if block ~= nil then
                    _trace_violation("collect_field:" .. tostring(f), "blocked: " .. block)
                    return {
                        action = "blocked",
                        field = f,
                        reason = block,
                        do_first = block,
                    }
                end

                local raw = args.value
                if raw == nil or _trim(raw) == "" then
                    -- First call: emit the elicitation prompt for the LLM to relay.
                    state.last_user_message = build_elicitation_prompt(f, nil)
                    return {
                        action = "elicit",
                        field = f,
                        message = build_elicitation_prompt(f, nil),
                        requested_schema = def.schema,
                        instruction = "Reply to the user with the EXACT text of `message`. Then wait for the user's reply, then call collect_field again with name='" .. f .. "' and value=<the user's reply>.",
                    }
                end

                local ok, normalized_or_err = validate_and_store(f, raw)
                if ok then
                    refresh_summaries()
                    local nxt = next_required_field()
                    local hint
                    if nxt == nil then
                        if all_requirements_met() then
                            hint = "All required fields are collected. Call done() with a one-line reason."
                        else
                            hint = "All elicitation fields done; complete any remaining disclosures or call done()."
                        end
                    else
                        hint = "Next required: " .. nxt .. ". Call collect_field(name='" .. nxt .. "') to elicit it."
                    end
                    return {
                        action = "accepted",
                        recorded = f,
                        value = normalized_or_err,
                        still_needed = state.still_needed,
                        next_hint = hint,
                    }
                else
                    -- Validation failure: re-elicit with the error baked into
                    -- the message so the LLM (and the user) sees why.
                    state.last_user_message = build_elicitation_prompt(f, normalized_or_err)
                    return {
                        action = "elicit",
                        field = f,
                        error = normalized_or_err,
                        message = build_elicitation_prompt(f, normalized_or_err),
                        requested_schema = def.schema,
                        instruction = "The previous value was rejected. Relay the new `message` verbatim and ask the user again.",
                    }
                end
            end,
        },
    },
    tools = {},
}

local function truthy(v)
    if v == true then return true end
    if v == false or v == nil then return false end
    if type(v) == "string" then return string.lower(v) == "true" or v == "1" end
    if type(v) == "number" then return v ~= 0 end
    return false
end

local function build_system_hint()
    refresh_summaries()
    local need = state.still_needed or "(unknown)"
    local pending_compliance = ""
    if not state.compliance_recording_done then
        pending_compliance = " First, deliver the recording/privacy disclosure (record_compliance with kind=recording_privacy)."
    elseif state.form_issue_category == ISSUE_BILLING and not state.compliance_fee_done then
        pending_compliance = " The billing branch also needs the fee_terms disclosure (record_compliance with kind=fee_terms) before billing_charge_acknowledged."
    end
    local nxt = next_required_field()
    local next_line = ""
    if nxt ~= nil then
        next_line = " Next field to elicit: " .. nxt .. " (call collect_field(name='" .. nxt .. "'))."
    end
    return "SYSTEM: Still needed: " .. need .. "." .. pending_compliance .. next_line
end

Procedure {
    input = {
        kickoff = field.string{default = "Hi, I'm calling about my account.", description = "Opening user line"},
        skip_hitl = field.boolean{default = false},
        mock_user_replies = field.array{required = false},
        max_turns = field.number{default = 58},
    },
    output = {
        completed                   = field.boolean{required = true},
        turns                       = field.number{required = true},
        issue_category              = field.string{required = false},
        account_email               = field.string{required = false},
        issue_summary               = field.string{required = false},
        callback_phone              = field.string{required = false},
        device_model                = field.string{required = false},
        billing_charge_acknowledged = field.string{required = false},
        plan_approval               = field.string{required = false},
        compliance_recording_done   = field.boolean{required = true},
        compliance_fee_done         = field.boolean{required = true},
        step_trace                  = field.array{required = true},
        violations                  = field.array{required = true},
    },
    function(input)
        for _, k in ipairs({
            "issue_category",
            "account_email",
            "issue_summary",
            "callback_phone",
            "device_model",
            "billing_charge_acknowledged",
            "plan_approval",
        }) do
            state["form_" .. k] = nil
        end
        state.compliance_recording_done = false
        state.compliance_fee_done = false
        state._assistant_transcript = ""
        state._step_trace = {}
        state._violations = {}
        state._done_step_logged = false
        refresh_summaries()

        local reply_queue = {}
        if truthy(input.skip_hitl) and input.mock_user_replies then
            for _, line in ipairs(input.mock_user_replies) do
                table.insert(reply_queue, line)
            end
        end

        local user_msg = input.kickoff or "Hello."
        local max_turns = input.max_turns or 58
        local turns = 0
        local round = 0
        state._agent_prompt_tokens = 0
        state._agent_completion_tokens = 0
        state._agent_total_tokens = 0

        local function record_agent_usage(result)
            if result ~= nil and result.usage ~= nil then
                state._agent_prompt_tokens = state._agent_prompt_tokens + (result.usage.prompt_tokens or 0)
                state._agent_completion_tokens = state._agent_completion_tokens + (result.usage.completion_tokens or 0)
                state._agent_total_tokens = state._agent_total_tokens + (result.usage.total_tokens or 0)
            end
        end

        local function user_message_for_display(raw)
            local s = tostring(raw or "")
            s = string.gsub(s, "^%s*(.-)%s*$", "%1")
            if s == "" then return "(empty message)" end
            if #s <= 96 then return s end
            return string.sub(s, 1, 93) .. "…"
        end

        local function print_flow_state()
            print("[Support flow]")
            print("  Recording disclosure done: " .. tostring(state.compliance_recording_done))
            print("  Fee disclosure done: " .. tostring(state.compliance_fee_done))
            print("  Still to collect: " .. tostring(state.still_needed))
            print("  Collected: " .. tostring(state.collected_summary))
        end

        local MAX_AUTO_CONTINUE = 3

        local function run_guide_once(msg)
            turns = turns + 1
            state.last_user_message = nil
            local guide_result = guide({message = msg})
            record_agent_usage(guide_result)
            local text = ""
            if state.last_user_message and #tostring(state.last_user_message) > 0 then
                text = tostring(state.last_user_message)
            elseif guide.output then
                text = tostring(guide.output)
            end
            if text == "" or text == "None" or string.find(text, "UsageStats", 1, true) then
                text = ""
            end
            return text
        end

        local function run_guide(call_label, msg, optional_user_echo)
            local text = run_guide_once(msg)

            -- If the LLM only emitted tool calls (e.g. an `accepted` collect_field with no
            -- chained elicit) the user has nothing to read. Nudge it forward up to
            -- MAX_AUTO_CONTINUE times to get a user-visible reply.
            local auto = 0
            while text == "" and not done_tool.called() and auto < MAX_AUTO_CONTINUE do
                if turns >= max_turns then break end
                auto = auto + 1
                refresh_summaries()
                local nxt = next_required_field()
                local sys_lines = {
                    "SYSTEM: Your previous response had no user-visible text.",
                    "Still needed: " .. tostring(state.still_needed or "(unknown)") .. ".",
                }
                if not state.compliance_recording_done then
                    table.insert(sys_lines, "First, deliver the recording/privacy disclosure (record_compliance with kind=recording_privacy).")
                elseif nxt ~= nil then
                    table.insert(sys_lines, "Call collect_field(name='" .. nxt .. "') to elicit the next required field, then relay its `message` verbatim.")
                elseif all_requirements_met() then
                    table.insert(sys_lines, "All requirements look satisfied; call done with a one-line reason.")
                else
                    table.insert(sys_lines, "Take the next action (chat_only, record_compliance, or collect_field).")
                end
                table.insert(sys_lines, "Do NOT submit a collect_field value the user has not given.")
                text = run_guide_once(table.concat(sys_lines, "\n"))
            end

            if text == "" then
                text = "(Assistant produced no user-visible text; check tool args.)"
            end
            state._last_assistant_text = text
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
                print("Stopped: max_turns reached.")
                break
            end

            refresh_summaries()
            if round > 1 then
                print("")
                print(string.rep("─", 48))
                print("")
            end
            print_flow_state()

            local hint = build_system_hint()
            local turn_msg = hint .. "\n\n" .. tostring(user_msg or "")

            run_guide("", turn_msg, user_msg)

            if done_tool.called() and not state._done_step_logged then
                _trace_step("done")
                state._done_step_logged = true
            end

            if done_tool.called() and not all_requirements_met() then
                print("[Procedure] done too early; nudging.\n")
                run_guide(
                    " · blocked",
                    "SYSTEM: Requirements are not complete. Continue collecting fields and disclosures before done. Still needed: "
                        .. tostring(state.still_needed or "(unknown)"),
                    nil
                )
            end

            if all_requirements_met() and not done_tool.called() then
                run_guide(
                    " · nudge",
                    "SYSTEM: All requirements are satisfied. Call done with a one-line reason.",
                    nil
                )
            end

            if truthy(input.skip_hitl) then
                if #reply_queue == 0 then
                    print("Stopped: no more mock_user_replies.")
                    break
                end
                user_msg = table.remove(reply_queue, 1)
            else
                local hitl_prompt = ""
                if state._last_assistant_text ~= nil and tostring(state._last_assistant_text) ~= "" then
                    hitl_prompt = "[Assistant]\n"
                        .. tostring(state._last_assistant_text)
                        .. "\n\n[User]\n"
                else
                    hitl_prompt = "[User]\n"
                end
                user_msg = Human.input({
                    message = hitl_prompt,
                })
            end

        until done_tool.called() and all_requirements_met()

        refresh_summaries()
        return {
            completed = done_tool.called() and all_requirements_met(),
            turns = turns,
            issue_category = state.form_issue_category,
            account_email = state.form_account_email,
            issue_summary = state.form_issue_summary,
            callback_phone = state.form_callback_phone,
            device_model = state.form_device_model,
            billing_charge_acknowledged = state.form_billing_charge_acknowledged,
            plan_approval = state.form_plan_approval,
            compliance_recording_done = state.compliance_recording_done == true,
            compliance_fee_done = state.compliance_fee_done == true,
            agent_usage = {
                prompt_tokens = state._agent_prompt_tokens or 0,
                completion_tokens = state._agent_completion_tokens or 0,
                total_tokens = state._agent_total_tokens or 0,
            },
            step_trace = state._step_trace or {},
            violations = state._violations or {},
        }
    end,
}
