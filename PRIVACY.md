# Epic MCP: Privacy & Data Use Disclosure

_Last updated: November 15, 2026_

| Question | Answer |
|---|---|
| What data is accessed? | Your medication list (FHIR MedicationRequest), the prescribing clinician's name and credentials (FHIR Practitioner), and basic demographics (FHIR Patient), used to identify your record. Nothing else. |
| Is access read-only? | Yes. |
| Where is data stored? | Only on your own computer. There is no developer-operated server or cloud storage. |
| Does the developer receive my data? | No. |
| Is data sold or shared with third parties? | No. The developer never has the data. Data goes only to the AI assistant **you** connect, under that assistant's terms. |
| Is data used for advertising, research or analytics? | No. |
| Is data de-identified or aggregated? | No. Nothing is collected. |
| How is data protected in transit? | HTTPS/TLS between your computer and your health system. OAuth 2.0 with PKCE for sign-in. |
| How long is data kept? | Tokens are kept until they expire or you delete them. Retrieved data is not saved to disk except where you choose to save it. |
| How do I revoke access or delete data? | Revoke access in MyChart (Linked Apps / Manage App Access), and delete Epic MCP's local token folder or uninstall it. |
| Who can I contact? | Open an issue at https://github.com/sacknovi/epic-mcp/issues. |
