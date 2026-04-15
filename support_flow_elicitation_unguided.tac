--[[
Support conversation flow — elicitation experiment (unguided baseline).

Goal: a deliberately less-prescriptive agent prompt so the LLM must track the workflow itself.
The procedure still enforces constraints via tool handlers (disclosures before identifiers,
branch constraints, etc.) and uses the same personas and evaluation harness as the other
support-flow procedures.

Run:  tactus run support_flow_elicitation_unguided.tac
Mock: tactus test support_flow_elicitation_unguided.tac --mock --param skip_hitl=true
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

local GUIDE_MODEL = "gpt-5.4-mini"

local BASE_SYSTEM_PROMPT = [[You are a customer support agent for a SaaS product.

Your goal is to help the user and collect the necessary structured information.

Tools (exactly one per turn): record_compliance | record_field | chat_only | done.
- Use record_compliance to log the recording/privacy disclosure (and fee terms if billing).
- Use record_field to store fields when the user provides them.
- Use done only when everything required is collected.

Be concise and helpful.]]

guide = Agent {
    name = "guide",
    provider = "openai",
    model = GUIDE_MODEL,
    tool_choice = "required",
    system_prompt = BASE_SYSTEM_PROMPT,

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
            description = "No new data to record; reply to user and advance the flow.",
            input = {
                reason = field.string{required = false},
                reply = field.string{required = true, description = "User-visible message."},
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
        {
            name = "record_field",
            description = "Store one support flow field.",
            input = {
                field = field.string{required = true},
                value = field.string{required = true},
                note_to_user = field.string{required = true},
            },
            handler = function(args)
                local f = (args.field or ""):gsub("^%s*(.-)%s*$", "%1"):lower()
                local val = (args.value or ""):gsub("^%s*(.-)%s*$", "%1")
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
                    return {ok = false, error = "Unknown field: " .. tostring(f)}
                end
                if f == "account_email" and not state.compliance_recording_done then
                    _trace_violation("field:account_email", "missing recording_privacy compliance")
                    return {ok = false, error = "Deliver recording_privacy disclosure (record_compliance) before account_email."}
                end
                if f == "device_model" and state.form_issue_category ~= ISSUE_TECH then
                    _trace_violation("field:device_model", "device_model only for technical issues")
                    return {ok = false, error = "device_model only for technical issues."}
                end
                if f == "billing_charge_acknowledged" then
                    if state.form_issue_category ~= ISSUE_BILLING then
                        _trace_violation("field:billing_charge_acknowledged", "billing_charge_acknowledged only for billing issues")
                        return {ok = false, error = "billing_charge_acknowledged only for billing issues."}
                    end
                    if not state.compliance_fee_done then
                        _trace_violation("field:billing_charge_acknowledged", "missing fee_terms compliance")
                        return {ok = false, error = "Deliver fee_terms (record_compliance) before billing acknowledgment."}
                    end
                end
                if f == "plan_approval" then
                    if state.form_issue_category == ISSUE_TECH then
                        if (state.form_device_model or "") == "" then
                            _trace_violation("field:plan_approval", "missing device_model (technical)")
                            return {ok = false, error = "Record device_model before plan_approval for technical issues."}
                        end
                    end
                    if state.form_issue_category == ISSUE_BILLING then
                        if not state.compliance_fee_done then
                            _trace_violation("field:plan_approval", "missing fee_terms compliance (billing)")
                            return {ok = false, error = "Complete fee disclosure path before plan_approval."}
                        end
                        if (state.form_billing_charge_acknowledged or "") ~= "yes" then
                            _trace_violation("field:plan_approval", "missing billing_charge_acknowledged (billing)")
                            return {ok = false, error = "Record billing_charge_acknowledged yes before plan_approval."}
                        end
                    end
                end
                if val == "" then
                    _trace_violation("field:" .. tostring(f), "empty value")
                    return {ok = false, error = "value is required"}
                end
                local validator = VALIDATORS[f]
                if validator and not validator(val) then
                    _trace_violation("field:" .. tostring(f), "validation failed")
                    return {ok = false, error = VALIDATOR_ERRORS[f]}
                end
                state["form_" .. f] = val
                _trace_step("field:" .. tostring(f))
                refresh_summaries()
                state.last_user_message = tostring(args.note_to_user or "")
                return {ok = true, field = f}
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

        local function print_flow_state()
            print("[Support flow]")
            print("  Recording disclosure done: " .. tostring(state.compliance_recording_done))
            print("  Fee disclosure done: " .. tostring(state.compliance_fee_done))
            print("  Still to collect: " .. tostring(state.still_needed))
            print("  Collected: " .. tostring(state.collected_summary))
        end

        local function user_message_for_display(raw)
            local s = tostring(raw or "")
            s = string.gsub(s, "^%s*(.-)%s*$", "%1")
            if s == "" then return "(empty message)" end
            if #s <= 96 then return s end
            return string.sub(s, 1, 93) .. "…"
        end

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
            if text == "" or text == "None" or string.find(text, "UsageStats", 1, true) then
                text = "(Assistant produced no user-visible text; check tool args.)"
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

            run_guide("", wrap_user_message(user_msg), user_msg)

            if done_tool.called() and not state._done_step_logged then
                _trace_step("done")
                state._done_step_logged = true
            end

            if done_tool.called() and not all_requirements_met() then
                print("[Procedure] done too early; nudging.\n")
                run_guide(
                    " · blocked",
                    wrap_user_message(
                        "SYSTEM: Requirements are not complete. Continue disclosures, branch-specific fields, and approval before done."
                    ),
                    nil
                )
            end

            if all_requirements_met() and not done_tool.called() then
                run_guide(
                    " · nudge",
                    wrap_user_message(
                        "SYSTEM: All requirements are satisfied. Call done with a one-line reason."
                    ),
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
                user_msg = Human.input({
                    prompt = "›: ",
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
            step_trace = state._step_trace or {},
            violations = state._violations or {},
        }
    end,
}

