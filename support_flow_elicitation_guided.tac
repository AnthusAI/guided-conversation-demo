--[[
Support conversation flow — elicitation experiment (guided).

This models MCP-style elicitation without using an MCP server:
- The procedure decides when it needs authoritative structured input.
- It asks the user for that data via an explicit "form" checkpoint (accept/decline/cancel).
- It validates and stores the result programmatically (same validators/enforcement as record_field).

The guide agent still handles natural-language disclosures and short plan explanations.

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
    device_model = function(s) return type(s) == "string" and #s >= 2 end,
    billing_charge_acknowledged = function(s) return s == "yes" end,
    plan_approval = function(s) return s == "yes" end,
}

local VALIDATOR_ERRORS = {
    issue_category              = "issue_category must be exactly general, billing, or technical.",
    account_email               = "Email must look like user@domain.ext.",
    issue_summary               = "Issue summary must be at least 5 characters.",
    callback_phone              = "Phone must be XXX-XXX-XXXX.",
    device_model                = "Device model is required for technical issues (at least 2 characters).",
    billing_charge_acknowledged = "Must record billing_charge_acknowledged as exactly yes after fee disclosure.",
    plan_approval               = "Must record plan_approval as exactly yes after explaining the plan.",
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
        "issue_category",
        "account_email",
        "issue_summary",
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

local function wrap_user_message(raw)
    return tostring(raw)
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

local function infer_issue_category_from_summary(summary)
    summary = tostring(summary or "")
    if _contains(summary, "charge") or _contains(summary, "charged") or _contains(summary, "subscription") then
        return ISSUE_BILLING
    end
    if _contains(summary, "vpn") or _contains(summary, "router") or _contains(summary, "wifi") then
        return ISSUE_TECH
    end
    return ISSUE_GENERAL
end

local function record_field_programmatically(field_name, value, note_to_user)
    local f = _lower(_trim(field_name))
    local val = _trim(value)
    local allowed = {
        issue_category = true,
        account_email = true,
        issue_summary = true,
        callback_phone = true,
        device_model = true,
        billing_charge_acknowledged = true,
        plan_approval = true,
    }
    if not allowed[f] then
        _trace_violation("field:" .. tostring(f), "unknown field")
        return false, "Unknown field: " .. tostring(f)
    end
    if f == "account_email" and not state.compliance_recording_done then
        _trace_violation("field:account_email", "missing recording_privacy compliance")
        return false, "Deliver recording_privacy disclosure (record_compliance) before account_email."
    end
    if f == "device_model" and state.form_issue_category ~= ISSUE_TECH then
        _trace_violation("field:device_model", "device_model only for technical issues")
        return false, "device_model only for technical issues."
    end
    if f == "billing_charge_acknowledged" then
        if state.form_issue_category ~= ISSUE_BILLING then
            _trace_violation("field:billing_charge_acknowledged", "billing_charge_acknowledged only for billing issues")
            return false, "billing_charge_acknowledged only for billing issues."
        end
        if not state.compliance_fee_done then
            _trace_violation("field:billing_charge_acknowledged", "missing fee_terms compliance")
            return false, "Deliver fee_terms (record_compliance) before billing acknowledgment."
        end
    end
    if f == "plan_approval" then
        if state.form_issue_category == ISSUE_TECH then
            if (state.form_device_model or "") == "" then
                _trace_violation("field:plan_approval", "missing device_model (technical)")
                return false, "Record device_model before plan_approval for technical issues."
            end
        end
        if state.form_issue_category == ISSUE_BILLING then
            if not state.compliance_fee_done then
                _trace_violation("field:plan_approval", "missing fee_terms compliance (billing)")
                return false, "Complete fee disclosure path before plan_approval."
            end
            if (state.form_billing_charge_acknowledged or "") ~= "yes" then
                _trace_violation("field:plan_approval", "missing billing_charge_acknowledged (billing)")
                return false, "Record billing_charge_acknowledged yes before plan_approval."
            end
        end
    end
    if val == "" then
        _trace_violation("field:" .. tostring(f), "empty value")
        return false, "value is required"
    end
    local validator = VALIDATORS[f]
    if validator and not validator(val) then
        _trace_violation("field:" .. tostring(f), "validation failed")
        return false, VALIDATOR_ERRORS[f]
    end
    state["form_" .. f] = val
    _trace_step("field:" .. tostring(f))
    refresh_summaries()
    state.last_user_message = tostring(note_to_user or "")
    return true, nil
end

local function parse_elicitation_response(raw)
    local action = nil
    local content = {}
    local s = tostring(raw or "")
    for line in string.gmatch(s, "[^\n]+") do
        local t = _trim(line)
        if t ~= "" then
            local k, v = t:match("^([%w_%-]+)%s*=%s*(.-)%s*$")
            if not k then
                k, v = t:match("^([%w_%-]+)%s*:%s*(.-)%s*$")
            end
            if k and v then
                k = _lower(k)
                if k == "action" then
                    action = _lower(v)
                else
                    content[k] = _trim(v)
                end
            end
        end
    end
    return action, content
end

local function elicitation_form(prompt_title, message, requested_schema, required_fields, attempts, reply_queue)
    attempts = attempts or 2
    local schema_text = tostring(requested_schema or "")
    local required_text = table.concat(required_fields or {}, ", ")

    local prompt = ""
        .. "[ELICITATION · FORM] " .. tostring(prompt_title or "Request") .. "\n"
        .. tostring(message or "") .. "\n\n"
        .. "Requested schema (informative):\n" .. schema_text .. "\n\n"
        .. "Reply format (exact; one per line):\n"
        .. "action=accept|decline|cancel\n"
        .. "field=value\n"
        .. "(required fields: " .. required_text .. ")\n"
        .. "Example:\n"
        .. "action=accept\n"
        .. (required_fields and required_fields[1] and (required_fields[1] .. "=...") or "field=value") .. "\n"

    for _ = 1, attempts do
        local raw
        if reply_queue ~= nil then
            if #reply_queue == 0 then
                return "cancel", {}
            end
            raw = table.remove(reply_queue, 1)
        else
            raw = Human.input({prompt = prompt})
        end
        local action, content = parse_elicitation_response(raw)
        if action == "decline" or action == "cancel" then
            return action, {}
        end
        if action ~= "accept" then
            -- treat malformed as cancel to avoid loops
            return "cancel", {}
        end
        local ok = true
        for _, f in ipairs(required_fields or {}) do
            if content[_lower(f)] == nil or content[_lower(f)] == "" then
                ok = false
                break
            end
        end
        if ok then
            return "accept", content
        end
    end
    return "cancel", {}
end

local GUIDE_MODEL = "gpt-5.4-mini"

local GUIDE_SYSTEM_PROMPT = [[You are a careful customer support agent.

You will help with disclosures and plan explanation. A separate structured "form" checkpoint may collect the user's details.

Tools (exactly one per turn): record_compliance | chat_only | done.]]

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
                state.last_user_message = "Okay — I’ve recorded this as complete."
                return {ok = true}
            end,
        },
        {
            name = "chat_only",
            description = "Reply to user (no structured recording).",
            input = {
                reply = field.string{required = true},
            },
            handler = function(args)
                state.last_user_message = tostring(args.reply or "")
                return {ok = true}
            end,
        },
        {
            name = "record_compliance",
            description = "After you spoke the disclosure aloud to the user, log it. note_to_user must match what the user heard.",
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
                local kind = tostring(args.kind or ""):gsub("^%s*(.-)%s*$", "%1"):lower()
                local note = tostring(args.note_to_user or ""):gsub("^%s*(.-)%s*$", "%1")
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
                    return {ok = false, error = "Unknown compliance kind."}
                end
                state.last_user_message = note
                return {ok = true, kind = kind}
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

        local reply_queue = nil
        if truthy(input.skip_hitl) and input.mock_user_replies then
            reply_queue = {}
            for _, line in ipairs(input.mock_user_replies) do
                table.insert(reply_queue, line)
            end
        end

        local turns = 0
        local round = 0
        local max_turns = input.max_turns or 58

        local function run_guide(call_label, msg)
            turns = turns + 1
            state.last_user_message = nil
            guide({message = msg})
            local text = tostring(state.last_user_message or "")
            if text == "" then
                text = "(Assistant produced no user-visible text; check tool args.)"
            end
            state._assistant_transcript = (state._assistant_transcript or "")
                .. "\n[TURN]" .. tostring(call_label) .. "\n" .. tostring(text) .. "\n"
            print("[Assistant" .. call_label .. "]\n" .. text)
        end

        -- 1) Disclosures (LLM-authored; enforced by tool handler ordering).
        round = round + 1
        if round > max_turns then
            return {completed = false, turns = turns, compliance_recording_done = false, compliance_fee_done = false, step_trace = {}, violations = {}}
        end
        run_guide(
            "",
            wrap_user_message(
                (input.kickoff or "Hello.") .. "\n\nSYSTEM: Start by delivering recording/privacy disclosure and logging it with record_compliance(recording_privacy)."
            )
        )

        -- 2) Elicit issue summary first (used to derive and then confirm issue_category).
        refresh_summaries()
        local act_sum, cont_sum = elicitation_form(
            "Issue summary",
            "Please briefly describe the problem you need help with.",
            '{\"type\":\"object\",\"properties\":{\"issue_summary\":{\"type\":\"string\",\"minLength\":5}},\"required\":[\"issue_summary\"]}',
            {"issue_summary"},
            2,
            reply_queue
        )
        if act_sum ~= "accept" then
            -- fall back to chat; leave incomplete
            return {
                completed = false,
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
                step_trace = state._step_trace or {},
                violations = state._violations or {},
            }
        end
        record_field_programmatically("issue_summary", cont_sum["issue_summary"], "Thanks — I’ve noted your issue summary.")

        local predicted = infer_issue_category_from_summary(state.form_issue_summary or "")
        local act_cat, cont_cat = elicitation_form(
            "Issue category confirmation",
            "To route you correctly, please confirm the category. Suggested default is based on your summary.",
            '{\"type\":\"object\",\"properties\":{\"issue_category\":{\"type\":\"string\",\"enum\":[\"general\",\"billing\",\"technical\"],\"default\":\"' .. tostring(predicted) .. '\"}},\"required\":[\"issue_category\"]}',
            {"issue_category"},
            2,
            reply_queue
        )
        if act_cat ~= "accept" then
            return {
                completed = false,
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
                step_trace = state._step_trace or {},
                violations = state._violations or {},
            }
        end
        record_field_programmatically("issue_category", cont_cat["issue_category"], "Got it — thanks.")

        -- 3) Core fields.
        local act_email, cont_email = elicitation_form(
            "Account email",
            "Please provide the email address on the account.",
            '{\"type\":\"object\",\"properties\":{\"account_email\":{\"type\":\"string\",\"format\":\"email\"}},\"required\":[\"account_email\"]}',
            {"account_email"},
            2,
            reply_queue
        )
        if act_email == "accept" then
            record_field_programmatically("account_email", cont_email["account_email"], "Thanks — I’ve recorded your account email.")
        end

        local act_phone, cont_phone = elicitation_form(
            "Callback phone",
            "Please provide a callback number in the format XXX-XXX-XXXX.",
            '{\"type\":\"object\",\"properties\":{\"callback_phone\":{\"type\":\"string\",\"pattern\":\"^\\\\\\\\d{3}-\\\\\\\\d{3}-\\\\\\\\d{4}$\"}},\"required\":[\"callback_phone\"]}',
            {"callback_phone"},
            2,
            reply_queue
        )
        if act_phone == "accept" then
            record_field_programmatically("callback_phone", cont_phone["callback_phone"], "Thanks — I’ve recorded your callback phone number.")
        end

        -- 4) Branch-dependent fields and disclosures.
        if state.form_issue_category == ISSUE_TECH then
            local act_dev, cont_dev = elicitation_form(
                "Device model",
                "Please provide the device or hardware model.",
                '{\"type\":\"object\",\"properties\":{\"device_model\":{\"type\":\"string\",\"minLength\":2}},\"required\":[\"device_model\"]}',
                {"device_model"},
                2,
                reply_queue
            )
            if act_dev == "accept" then
                record_field_programmatically("device_model", cont_dev["device_model"], "Thanks — I’ve recorded the device model.")
            end
        elseif state.form_issue_category == ISSUE_BILLING then
            run_guide(
                " · fee",
                wrap_user_message(
                    "SYSTEM: Deliver the fee terms disclosure for billing and log it with record_compliance(fee_terms)."
                )
            )
            local act_ack, cont_ack = elicitation_form(
                "Billing fee acknowledgment",
                "Do you acknowledge the billing fee terms? (Answer yes to proceed.)",
                '{\"type\":\"object\",\"properties\":{\"billing_charge_acknowledged\":{\"type\":\"string\",\"enum\":[\"yes\"]}},\"required\":[\"billing_charge_acknowledged\"]}',
                {"billing_charge_acknowledged"},
                2,
                reply_queue
            )
            if act_ack == "accept" then
                record_field_programmatically("billing_charge_acknowledged", cont_ack["billing_charge_acknowledged"], "Thanks — I’ve recorded your acknowledgment.")
            end
        end

        -- 5) Plan explanation (LLM-authored), then structured approval.
        run_guide(
            " · plan",
            wrap_user_message(
                "SYSTEM: Explain a short resolution plan appropriate to the issue. Then ask the user to approve it."
            )
        )
        local act_appr, cont_appr = elicitation_form(
            "Plan approval",
            "Do you approve the proposed plan? (Answer yes to proceed.)",
            '{\"type\":\"object\",\"properties\":{\"plan_approval\":{\"type\":\"string\",\"enum\":[\"yes\"]}},\"required\":[\"plan_approval\"]}',
            {"plan_approval"},
            2,
            reply_queue
        )
        if act_appr == "accept" then
            record_field_programmatically("plan_approval", cont_appr["plan_approval"], "Great — I’ve recorded your approval.")
        end

        -- 6) Finish.
        refresh_summaries()
        if all_requirements_met() then
            done_tool({reason = "Elicitation-style support flow complete."})
            _trace_step("done")
            state._done_step_logged = true
        end

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
            step_trace = state._step_trace or {},
            violations = state._violations or {},
        }
    end,
}

