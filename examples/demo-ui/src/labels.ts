/** Plain-language names for the machine vocabulary. The codes stay authoritative;
 *  these are only what a human reads first.
 *
 *  Every key here has to be a code something actually emits: the example
 *  servers, the SDK, or the analysis behind it. A label for a code nothing
 *  sends is a claim about a rule that does not exist, and `label()` already
 *  falls back to the raw code for anything not listed. */

/** The actions the example's `propose_action` tool declares. */
export const ACTION_LABEL: Record<string, string> = {
  'password.reset': 'Reset their own password',
  'password.reset_other': "Reset someone else's password",
  'mfa.reset': 'Reset two-factor',
  'account.disable': 'Disable an account',
  'group.add_member': 'Add someone to a group',
  'device.enroll': 'Enroll a device',
  'mailbox.add_forwarding': 'Forward a mailbox',
}

export const REASON_LABEL: Record<string, string> = {
  check_not_implemented: 'No action check ran',
}

/** The signals the analysis reports on a caller's turn. */
export const SIGNAL_LABEL: Record<string, string> = {
  third_party_instructing_live: 'Someone else is telling them what to say',
  outage_or_exec_pressure: "Says it can't wait: an outage, a deadline, a boss",
  skip_the_ticket: 'Wants to skip the approval',
  invented_authority: 'Cites an approval nobody can see',
  new_device_plus_mfa: 'New device, and wants two-factor changed',
}

export const RESOLUTION_LABEL: Record<string, string> = {
  resolve_on_call: 'Resolve on the call',
}

/** Why GET /sop came back with no runbook, procedures or controls. Each of
 *  these is a code the example server sends, and the honest thing to render is
 *  the code's meaning rather than something in its place. */
export const CONTEXT_REASON_LABEL: Record<string, string> = {
  // The API answered with content. Whatever it did not include is simply not
  // configured, and needs no explaining.
  ok: '',
  organisation_context_empty:
    'This organisation has no runbook, procedures or controls configured.',
  context_endpoint_not_found:
    'The API does not yet hand an organisation key its runbook, procedures or controls.',
  api_key_rejected:
    'The API refused this key when asked for the organisation context.',
  api_key_missing: 'The example server has no DEEPTRUST_API_KEY configured.',
  backend_unreachable: 'The DeepTrust API could not be reached.',
  backend_error: 'The DeepTrust API returned an error for this request.',
  malformed_response: 'The response could not be read.',
}

export const label = (m: Record<string, string>, k: string) =>
  m[k] ?? k.replace(/[._]/g, ' ')
