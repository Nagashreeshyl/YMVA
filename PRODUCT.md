# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

- **Admin (primary, daily):** the Yellow Metal team member who builds and tunes voice agents, connects telephony providers and tools, runs outbound campaigns, and reviews calls, transcripts and recordings. Works on a laptop.
- **CEO (viewer):** checks results (call volumes, connect rate, promise-to-pay outcomes, cost, live calls) mostly on the Overview, often from a phone. Read-only.

No other users are planned for now.

## Product Purpose

YMVA (Yellow Metal Voice Agent) is Yellow Metal's internal platform for AI phone agents. The first agent reminds gold-loan customers about interest and loan repayments by outbound call; customer-support and other reminder agents follow. Success means calls that sound natural in the customer's own language, reliably capture outcomes (e.g. promise-to-pay date) into Sheets/CRM, and give the team a complete, trustworthy history of every call and change.

## Positioning

An in-house alternative to Vapi/Retell/Dograh built around Yellow Metal's needs: Indian multilingual calling (Kannada, Telugu, Tamil, Hindi, English with mid-call switching), any telephony provider (Plivo, Vobiz, Exotel, Twilio, Telnyx, Vonage, SIP), and agents built from plain-English instructions.

## Operating Context

- Yellow Metal is an RBI-licensed NBFC offering gold loans; customers mainly in Karnataka, Andhra Pradesh and Telangana.
- Agents are built three ways: manual form, an assistant chat that edits an agent, and "build with AI" from described conditions.
- Data lives in Google Sheets, databases and CRMs; agents read and write them via tools and MCP.
- Two-person usage: admin on laptop, CEO checking on phone.

## Capabilities and Constraints

- Roles: admin (full control) and viewer (read-only).
- Every call, transcript, recording, tool call and configuration change is kept in history.
- Collections calls must respect RBI calling hours (8 AM–7 PM) and do-not-call lists; recording announcement is configurable per agent.
- Customer phone numbers and loan details are sensitive; mask where full values are not needed.

## Brand Commitments

- Follows yellowmetal.co: black ink on white, light-grey surfaces, gold (#F5C542) as the only accent, Inter for UI text, Bricolage Grotesque for display, pill-shaped buttons. (Binding constraint from the user.)
- Voice: simple, reassuring, transparent — matching Yellow Metal's "no hidden charges" tone.

## Evidence on Hand

- No real call data yet; dashboards must show honest empty states, never sample/fake numbers.
- Logo not yet in the repo.

## Product Principles

1. Trust over flash: every number shown is real and traceable to calls.
2. Fast to scan: the CEO should understand the day in seconds.
3. Nothing silently lost: every change and call is recorded and recoverable.
4. Simple by default: one clear primary action per screen; advanced settings tucked away.

## Accessibility & Inclusion

WCAG 2.1 AA contrast and full keyboard use; readable on a phone for Overview and call history.
