--[[
Support conversation flow — experiment two: rigid orchestrator arm.

The agent is driven through the conversation by a single ``run_phase``
orchestrator tool. Each turn the agent calls ``run_phase`` (no arguments) to
ask "what should I do next?" and receives a structured payload describing
exactly one next action. Form-filling actions are delegated to the existing
``collect_field`` sub-tool (the same elicitation tool used in experiment one).

The "rigid" arm enforces a strict canonical phase order. The orchestrator
never returns a chat-only acknowledgment of the user's preferred topic; once
the privacy disclosure is delivered the orchestrator immediately starts
asking for structured fields in a fixed sequence. Every elicitation prompt
is sterile (no topic acknowledgment, no preamble). Under the impatient
simulator this is what produces the ``hung_up`` outcome we are studying.

Run:  tactus run support_flow_orchestrated_rigid.tac
Mock: tactus test support_flow_orchestrated_rigid.tac --mock --param skip_hitl=true
--]]

local done_tool = require("tactus.tools.done")

local ARM_LABEL = "rigid"
local HUNG_UP_SENTINEL = "[USER HUNG UP — patience exhausted]"

local ISSUE_GENERAL = "general"
local ISSUE_BILLING = "billing"
local ISSUE_TECH = "technical"

local FIELD_LABEL = {
    issue_summary               = "Brief issue summary",
    issue_category              = "Issue category (general, billing, or technical)",
    account_email               = "Account email on file",
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
        local refusals = {
            ["yes"] = true, ["no"] = true, ["true"] = true, ["false"] = true,
            ["sure"] = true, ["ok"] = true, ["okay"] = true, ["yeah"] = true,
            ["yep"] = true, ["nope"] = true, ["pass"] = true, ["skip"] = true,
            ["none"] = true, ["null"] = true, ["nil"] = true,
        }
        if refusals[low] then return false end
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

local FIELD_DEFS = {
    issue_summary = {
        title  = "Issue summary",
        prompt = "Please briefly describe the problem you need help with.",
        schema = { type = "string", minLength = 5,
                   description = "Short free-text description of the customer's problem." },
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

-- The strict canonical phase order for the rigid arm.
local CANONICAL_FIELD_ORDER = {
    "issue_summary",
    "issue_category",
    "account_email",
    "callback_phone",
}

local function _ensure_trace_tables()
    state._step_trace = state._step_trace or {}
    state._violations = state._violations or {}
    state._phase_trace = state._phase_trace or {}
end

local function _trace_step(token)
    _ensure_trace_tables()
    table.insert(state._step_trace, tostring(token))
end

local function _trace_violation(action_token, reason)
    _ensure_trace_tables()
    table.insert(state._violations, {action = tostring(action_token), reason = tostring(reason or "")})
end

local function _trace_phase(token)
    _ensure_trace_tables()
    table.insert(state._phase_trace, tostring(token))
end

local function get_required_fields()
    local req = {}
    for _, f in ipairs(CANONICAL_FIELD_ORDER) do
        table.insert(req, f)
    end
    local cat = state.form_issue_category
    if cat == ISSUE_TECH then
        table.insert(req, "device_model")
    end
    if cat == ISSUE_BILLING then
        table.insert(req, "billing_charge_acknowledged")
    end
    table.insert(req, "plan_approval")
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

local function blocked_reason(field_name)
    local f = _lower(_trim(field_name))
    if not FIELD_DEFS[f] then
        return "Unknown field: " .. tostring(f) .. "."
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
            return "Deliver the fee_terms disclosure before billing_charge_acknowledged."
        end
    end
    if f == "plan_approval" then
        if not state.plan_explained then
            return "Explain the proposed resolution plan (chat_only or via run_phase action='say') before plan_approval."
        end
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

-- Decide the next phase the rigid orchestrator wants the agent to execute.
-- Returns a table describing one action; the orchestrator tool wraps this in
-- a payload for the LLM to act on.
local function next_phase()
    refresh_summaries()
    if not state.compliance_recording_done then
        return {
            kind = "compliance",
            compliance_kind = "recording_privacy",
        }
    end
    for _, f in ipairs(CANONICAL_FIELD_ORDER) do
        local v = state["form_" .. f]
        if v == nil or v == "" then
            return { kind = "elicit", field = f }
        end
    end
    -- Branch-specific steps.
    local cat = state.form_issue_category
    if cat == ISSUE_BILLING then
        if not state.compliance_fee_done then
            return { kind = "compliance", compliance_kind = "fee_terms" }
        end
        if (state.form_billing_charge_acknowledged or "") == "" then
            return { kind = "elicit", field = "billing_charge_acknowledged" }
        end
    elseif cat == ISSUE_TECH then
        if (state.form_device_model or "") == "" then
            return { kind = "elicit", field = "device_model" }
        end
    end
    -- Plan explanation must precede plan_approval elicitation.
    if (state.form_plan_approval or "") == "" then
        if not state.plan_explained then
            return { kind = "say_plan" }
        end
        return { kind = "elicit", field = "plan_approval" }
    end
    if all_requirements_met() then
        return { kind = "done" }
    end
    -- Defensive fallback (should not normally reach here).
    return { kind = "say_followup" }
end

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

-- Lean system prompt: the orchestrator drives the flow. The agent's job each
-- turn is to call run_phase to learn what to do, then carry it out using
-- collect_field / record_compliance / chat_only / done.
local GUIDE_SYSTEM_PROMPT = [[You are a careful customer support agent.

A run_phase orchestrator tool drives this conversation. Each turn the
procedure pre-fetches the orchestrator's next action for you and embeds it
in the SYSTEM line at the top of the user-role message. Read that ORCHESTRATOR
ACTION block and execute the indicated tool call this turn.

The orchestrator returns one of these actions:

- action="say":               call chat_only(reply=<message verbatim or close to it>).
- action="elicit_field":      call collect_field(name=<field>) (no value) so the
                              elicitation prompt is shown to the user. On the next
                              turn the user reply arrives in the user message; call
                              collect_field(name=<field>, value=<user reply verbatim>).
- action="record_compliance": read aloud the disclosure in `message`, then call
                              record_compliance(kind=<kind>, note_to_user=<message>).
- action="done":              call done(reason=<short reason>).

You MAY also call run_phase(note=...) yourself if you want to re-query the
orchestrator (it returns the same payload). It is provided for inspection.

The orchestrator is RIGID: do not improvise extra phases, do not chat outside
of what it tells you to. Do not pass values to collect_field the user did not
give. Per turn you call exactly one tool.

Tools:
- collect_field(name [, value])   — MCP-style elicitation sub-tool.
- record_compliance(kind, note)   — log a disclosure you just spoke aloud.
- chat_only(reply)                — non-recording user-visible message.
- done(reason)                    — finish.
- run_phase(note?)                — orchestrator inspection (optional).
]]

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
                    return {
                        ok = false,
                        error = "Cannot call done yet. Still needed: " .. tostring(state.still_needed or "(unknown)"),
                    }
                end
                done_tool({reason = tostring(args.reason or "Support flow complete.")})
                state.last_user_message = "Okay — I've recorded this as complete."
                return {ok = true}
            end,
        },
        {
            name = "chat_only",
            description = "Reply to the user without recording structured data.",
            input = {
                reply = field.string{required = true, description = "User-visible message."},
            },
            handler = function(args)
                state.last_user_message = tostring(args.reply or "")
                local cp = state._cached_phase
                if cp and cp.kind == "say_plan" then
                    state.plan_explained = true
                    _trace_step("plan_explained")
                end
                return {ok = true}
            end,
        },
        {
            name = "record_compliance",
            description = "After you spoke a regulatory disclosure aloud to the user, log it.",
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
                    return {ok = false, error = "note_to_user must be at least 12 characters."}
                end
                if kind == "recording_privacy" then
                    state.compliance_recording_done = true
                    _trace_step("compliance:recording_privacy")
                elseif kind == "fee_terms" then
                    if state.form_issue_category ~= ISSUE_BILLING then
                        _trace_violation("compliance:fee_terms", "fee_terms only for billing issues")
                        return {ok = false, error = "fee_terms only applies when issue_category=billing."}
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
            description = "MCP-style elicitation tool. First call with just `name` to elicit; second call with `name` AND `value` to submit the user's reply.",
            input = {
                name = field.string{required = true,
                    description = "Field to collect."},
                value = field.string{required = false,
                    description = "User's reply, verbatim. Omit on first call (eliciting); include on follow-up (submitting)."},
            },
            handler = function(args)
                local f = _lower(_trim(args.name))
                local def = FIELD_DEFS[f]
                if def == nil then
                    _trace_violation("collect_field:" .. tostring(f), "unknown field")
                    return { action = "error", reason = "Unknown field: " .. tostring(f) }
                end
                local block = blocked_reason(f)
                if block ~= nil then
                    _trace_violation("collect_field:" .. tostring(f), "blocked: " .. block)
                    return { action = "blocked", field = f, reason = block, do_first = block }
                end
                local raw = args.value
                if raw == nil or _trim(raw) == "" then
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
                    return {
                        action = "accepted",
                        recorded = f,
                        value = normalized_or_err,
                        still_needed = state.still_needed,
                        next_hint = "Call run_phase() to receive the next orchestrator action.",
                    }
                else
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
        {
            name = "run_phase",
            description = "Top-level orchestrator. Call once per turn to learn the next action. Returns one of: action='say' | 'elicit_field' | 'record_compliance' | 'done'.",
            input = {
                note = field.string{required = false, description = "Optional free-text note for tracing; not used by the orchestrator."},
            },
            handler = function(args)
                local phase = next_phase()
                _trace_phase("rigid:" .. tostring(phase.kind)
                    .. (phase.field and (":" .. phase.field) or "")
                    .. (phase.compliance_kind and (":" .. phase.compliance_kind) or ""))
                if phase.kind == "compliance" then
                    local ck = phase.compliance_kind
                    local msg
                    if ck == "recording_privacy" then
                        msg = "Before we go further, please note this call may be recorded for quality and training purposes, and your account data will only be used to resolve this support request."
                    else
                        msg = "Heads-up: there is a $29.99 research fee that will be credited back if a billing error is confirmed. Please acknowledge so I can continue."
                    end
                    return {
                        action = "record_compliance",
                        kind = ck,
                        message = msg,
                        instruction = "Read the disclosure aloud (use record_compliance with kind=" .. ck .. " and note_to_user=<message verbatim>). Do NOT add any topic acknowledgment.",
                    }
                elseif phase.kind == "elicit" then
                    local f = phase.field
                    local def = FIELD_DEFS[f]
                    return {
                        action = "elicit_field",
                        field = f,
                        message = build_elicitation_prompt(f, nil),
                        requested_schema = def.schema,
                        instruction = "Call collect_field(name='" .. f .. "') to elicit. Do NOT add any topic preamble or empathic acknowledgment — relay the elicitation message verbatim.",
                    }
                elseif phase.kind == "say_plan" then
                    local plan_msg = "Here is the proposed resolution plan: I will open a ticket with the details you provided and follow up by phone within one business day. Do you approve?"
                    return {
                        action = "say",
                        message = plan_msg,
                        instruction = "Call chat_only(reply=<message verbatim>). Do NOT pre-record plan_approval; wait for the user's explicit yes.",
                    }
                elseif phase.kind == "say_followup" then
                    return {
                        action = "say",
                        message = "Thanks for your patience. One moment.",
                        instruction = "Call chat_only(reply=<message>).",
                    }
                elseif phase.kind == "done" then
                    return {
                        action = "done",
                        reason = "All requirements satisfied.",
                        instruction = "Call done(reason='Support flow complete.').",
                    }
                else
                    return { action = "error", reason = "unknown phase kind" }
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

local function _phase_to_instruction(phase)
    if phase.kind == "compliance" then
        local ck = phase.compliance_kind
        local msg
        if ck == "recording_privacy" then
            msg = "Before we go further, please note this call may be recorded for quality and training purposes, and your account data will only be used to resolve this support request."
        else
            msg = "Heads-up: there is a $29.99 research fee that will be credited back if a billing error is confirmed. Please acknowledge so I can continue."
        end
        return {
            action = "record_compliance",
            summary = "Read the regulatory disclosure aloud and log it.",
            tool_call = "record_compliance(kind='" .. ck .. "', note_to_user=<message verbatim>)",
            message = msg,
        }
    elseif phase.kind == "elicit" then
        local f = phase.field
        return {
            action = "elicit_field",
            summary = "Elicit the next required field.",
            tool_call = "collect_field(name='" .. f .. "')",
            message = build_elicitation_prompt(f, nil),
            field = f,
        }
    elseif phase.kind == "say_plan" then
        return {
            action = "say",
            summary = "Explain the proposed resolution plan to the user.",
            tool_call = "chat_only(reply=<plan explanation verbatim>)",
            message = "Here is the proposed resolution plan: I will open a ticket with the details you provided and follow up by phone within one business day. Do you approve?",
        }
    elseif phase.kind == "say_followup" then
        return {
            action = "say",
            summary = "Filler reply.",
            tool_call = "chat_only(reply=<message>)",
            message = "Thanks for your patience. One moment.",
        }
    elseif phase.kind == "done" then
        return {
            action = "done",
            summary = "All requirements satisfied; finish the intake.",
            tool_call = "done(reason='Support flow complete.')",
            message = "Support flow complete.",
        }
    end
    return {
        action = "say",
        summary = "(unknown phase) — produce a brief courteous reply.",
        tool_call = "chat_only(reply=<short reply>)",
        message = "One moment.",
    }
end

local function build_system_hint()
    refresh_summaries()
    local phase = next_phase()
    local instr = _phase_to_instruction(phase)
    -- Cache for the run_phase tool so it returns the same payload.
    state._cached_phase = phase
    state._cached_instruction = instr
    local lines = {
        "SYSTEM: ORCHESTRATOR ACTION (RIGID arm).",
        "Action: " .. instr.action,
        "Summary: " .. instr.summary,
        "Required tool call this turn: " .. instr.tool_call,
        "Message to relay (verbatim, or as close as feasible):",
        "    " .. tostring(instr.message),
        "Still needed: " .. tostring(state.still_needed or "(unknown)") .. ".",
        "Constraints: do NOT add empathic preambles, topic acknowledgments, or off-script chat. Stay sterile and procedural.",
    }
    return table.concat(lines, "\n")
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
        hung_up                     = field.boolean{required = true},
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
        phase_trace                 = field.array{required = true},
        violations                  = field.array{required = true},
        arm                         = field.string{required = true},
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
        state.plan_explained = false
        state._assistant_transcript = ""
        state._step_trace = {}
        state._phase_trace = {}
        state._violations = {}
        state._done_step_logged = false
        state._hung_up = false
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
            print("[Support flow · " .. ARM_LABEL .. "]")
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
            local auto = 0
            while text == "" and not done_tool.called() and auto < MAX_AUTO_CONTINUE do
                if turns >= max_turns then break end
                auto = auto + 1
                refresh_summaries()
                local sys_lines = {
                    "SYSTEM: Your previous response had no user-visible text.",
                    "Call run_phase() now to learn what to do next, then carry out exactly that action.",
                    "Still needed: " .. tostring(state.still_needed or "(unknown)") .. ".",
                }
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
                    "SYSTEM: Requirements are not complete. Continue calling run_phase() and following its instructions. Still needed: "
                        .. tostring(state.still_needed or "(unknown)"),
                    nil
                )
            end

            if all_requirements_met() and not done_tool.called() then
                run_guide(
                    " · nudge",
                    "SYSTEM: All requirements are satisfied. Call run_phase() now; it will return action='done'.",
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

            -- Detect impatient-mode hangup sentinel from the simulator and end early.
            if type(user_msg) == "string" and string.find(user_msg, HUNG_UP_SENTINEL, 1, true) then
                print("[Procedure] User hung up (patience exhausted).")
                state._hung_up = true
                _trace_step("hung_up")
                break
            end

        until done_tool.called() and all_requirements_met()

        refresh_summaries()
        return {
            completed = done_tool.called() and all_requirements_met(),
            hung_up = state._hung_up == true,
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
            phase_trace = state._phase_trace or {},
            violations = state._violations or {},
            arm = ARM_LABEL,
        }
    end,
}
