--[[
Support conversation flow — deterministic scripted baseline (Arm C).

This arm removes the LLM entirely from the agent path. A Tactus Procedure walks
the workflow directly: it delivers each regulatory disclosure, then elicits each
required field via Human.input with a prompt that includes the
`[ELICITATION · FORM]` sentinel the simulator keys off. The procedure's Lua
validators and extractors are the same ones the Experiment-1 guided arm uses;
the only change versus that arm is that there is no Agent {} block and no LLM
calls in the agent path.

Role in the paper: a construct-validity floor (Appendix C). Its ideal-mode
cell bounds how much of the guided arm's headline gain is attributable to
the benchmark's structure rather than to the architectural intervention.

Run:  tactus run support_flow_scripted_baseline.tac
Mock: tactus test support_flow_scripted_baseline.tac --mock --param skip_hitl=true
--]]

local done_tool = require("tactus.tools.done")

local ARM_LABEL = "scripted"
local HUNG_UP_SENTINEL = "[USER HUNG UP — patience exhausted]"

local ISSUE_GENERAL = "general"
local ISSUE_BILLING = "billing"
local ISSUE_TECH = "technical"

local MAX_ATTEMPTS_PER_FIELD = 3  -- matches the .tac arms' retry budget

-- Disclosure strings. Must be >= 12 chars (matches guided arm's length check).
local RECORDING_DISCLOSURE =
    "Before we go further, please note this call may be recorded for quality "
    .. "and training purposes, and your account data will only be used to "
    .. "resolve this support request."

local FEE_TERMS_DISCLOSURE =
    "Heads-up: there is a $29.99 research fee that will be credited back if a "
    .. "billing error is confirmed. Please acknowledge so I can continue."

local PLAN_EXPLANATION =
    "Here is the proposed resolution plan: I will open a ticket with the "
    .. "details you provided and follow up by phone within one business day. "
    .. "Do you approve?"

local FIELD_DEFS = {
    issue_summary = {
        title  = "Issue summary",
        prompt = "Please briefly describe the problem you need help with.",
    },
    issue_category = {
        title  = "Issue category",
        prompt = "Which best describes your issue? Choose one of: general, billing, or technical.",
    },
    account_email = {
        title  = "Account email",
        prompt = "Please provide the email address on the account.",
    },
    callback_phone = {
        title  = "Callback phone",
        prompt = "Please provide a callback number in the format XXX-XXX-XXXX (digits and dashes only).",
    },
    device_model = {
        title  = "Device model",
        prompt = "Please provide the device or hardware model (example: ACME Router X200).",
    },
    billing_charge_acknowledged = {
        title  = "Billing fee acknowledgment",
        prompt = "Do you acknowledge the billing fee terms? Answer with exactly: yes",
    },
    plan_approval = {
        title  = "Plan approval",
        prompt = "Do you approve the proposed resolution plan? Answer with exactly: yes",
    },
}

-- Validators ported verbatim from support_flow_elicitation_guided.tac.
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

-- Extractors ported verbatim from support_flow_elicitation_guided.tac.
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

local function _ensure_state()
    state._step_trace = state._step_trace or {}
end

local function _trace(token)
    _ensure_state()
    table.insert(state._step_trace, tostring(token))
end

local function truthy(v)
    if v == true then return true end
    if v == false or v == nil then return false end
    if type(v) == "string" then return string.lower(v) == "true" or v == "1" end
    if type(v) == "number" then return v ~= 0 end
    return false
end

Procedure {
    input = {
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
        state._step_trace = {}
        state._hung_up = false

        local max_turns = input.max_turns or 58
        state._turns = 0

        local reply_queue = {}
        if truthy(input.skip_hitl) and input.mock_user_replies then
            for _, line in ipairs(input.mock_user_replies) do
                table.insert(reply_queue, line)
            end
        end

        -- Ask the simulator (or the mock queue) with `message`; return the reply string.
        -- On a hung-up sentinel, sets state._hung_up and returns nil.
        local function ask(message)
            if state._turns >= max_turns then
                return nil
            end
            state._turns = state._turns + 1
            local reply
            if truthy(input.skip_hitl) then
                if #reply_queue == 0 then
                    return nil
                end
                reply = table.remove(reply_queue, 1)
            else
                reply = Human.input({message = message})
            end
            if type(reply) == "string" and string.find(reply, HUNG_UP_SENTINEL, 1, true) then
                state._hung_up = true
                _trace("hung_up")
                return nil
            end
            return reply
        end

        -- Deliver a regulatory disclosure. The reply is discarded; we only need
        -- the simulator to acknowledge a turn.
        local function deliver_disclosure(text)
            local reply = ask(text)
            return state._hung_up == false and reply ~= nil
        end

        -- Elicit one field up to MAX_ATTEMPTS_PER_FIELD times. Returns true on
        -- success (field stored in state.form_<name>), false on budget exhaustion
        -- or hang-up.
        local function elicit(field_name)
            local err_msg = nil
            for attempt = 1, MAX_ATTEMPTS_PER_FIELD do
                if state._turns >= max_turns or state._hung_up then return false end
                local prompt = build_elicitation_prompt(field_name, err_msg)
                local reply = ask(prompt)
                if reply == nil then return false end
                local normalized = _extract_for_field(field_name, reply)
                local validator = VALIDATORS[field_name]
                if normalized ~= nil and normalized ~= "" and (not validator or validator(normalized)) then
                    state["form_" .. field_name] = normalized
                    _trace("field:" .. field_name)
                    return true
                end
                err_msg = VALIDATOR_ERRORS[field_name]
            end
            return false
        end

        local function early_exit()
            return {
                completed = false,
                hung_up = state._hung_up == true,
                turns = state._turns,
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
                arm = ARM_LABEL,
            }
        end

        -- 1. Recording / privacy disclosure.
        if not deliver_disclosure(RECORDING_DISCLOSURE) then return early_exit() end
        state.compliance_recording_done = true
        _trace("compliance:recording_privacy")

        -- 2. Issue category (first, because the branch depends on it).
        if not elicit("issue_category") then return early_exit() end

        -- 3. Core identifiers in fixed order (after compliance disclosure).
        if not elicit("account_email") then return early_exit() end
        if not elicit("issue_summary") then return early_exit() end
        if not elicit("callback_phone") then return early_exit() end

        -- 4. Branch-specific steps.
        if state.form_issue_category == ISSUE_TECH then
            if not elicit("device_model") then return early_exit() end
        elseif state.form_issue_category == ISSUE_BILLING then
            if not deliver_disclosure(FEE_TERMS_DISCLOSURE) then return early_exit() end
            state.compliance_fee_done = true
            _trace("compliance:fee_terms")
            if not elicit("billing_charge_acknowledged") then return early_exit() end
        end

        -- 5. Plan explanation + approval.
        if not deliver_disclosure(PLAN_EXPLANATION) then return early_exit() end
        if not elicit("plan_approval") then return early_exit() end

        -- 6. Done.
        done_tool({reason = "Support flow complete."})
        _trace("done")

        return {
            completed = true,
            hung_up = state._hung_up == true,
            turns = state._turns,
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
            arm = ARM_LABEL,
        }
    end,
}
