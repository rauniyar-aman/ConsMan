# Frontdesk and counseling workflow

Administrators create staff accounts in Settings. Select **Frontdesk Officer** and the office branch. Frontdesk officers can see branch visitors, assist registration, assign a counselor, upload documents, and complete follow-ups. They cannot change counseling decisions, manage accounts, view finance, or convert leads.

## QR codes for all staff

Open **Visitor QR codes** to display or download the branch Wi-Fi and registration QRs. The same library is available inside a visitor profile under **Visitor QR**. Visitors scan Wi-Fi first, then the registration form.

Administrators create WhatsApp QRs in QR Studio. Choose **Visible to staff** to assign the QR to the appropriate counselor or officer, or choose **Shared office WhatsApp**. Staff see their assigned WhatsApp QRs and shared office codes. QR editing remains with administrators and branch managers.

## Paper registration

Frontdesk opens **Staff-assisted intake** (or Add person) and enters the visitor's paper answers, including academic background, test scores, study preferences, alternate contact, and consent. **Save answers and send WhatsApp OTP** saves an unverified submission. The visitor reads their code to frontdesk, who enters it and selects **Verify visitor number**. Incorrect codes do not create a lead. Resending uses the existing cooldown and attempt limits.

After verification, open the profile and select **Assign counselor**, choosing the branch counselor appropriate for the visitor's destination and recording the assignment reason. Duplicate records requiring review appear in Intake review.

## Counseling and follow-up

The profile opens **Counseling**. The counselor records the discussion summary, key decisions, document checklist, pending documents, next step, package/service, and remarks. Use **Lifecycle / priority** above the panel to mark the lead status and Hot/Warm/Cold priority.

Optionally schedule a follow-up with a date in Nepal time, channel, assigned frontdesk officer or counselor, and specific discussion instructions. The assigned member sees those instructions in Follow-ups, records the outcome, and completes the follow-up. Delegated calls remain with their assigned caller when the primary counselor changes.

## Optional documents

No document upload is needed to save counseling or register a visitor. The counselor can display their WhatsApp QR so the visitor can send files. In **Profile documents**, add an optional document and upload the received PDF, PNG or JPEG (under 5 MB).

For physical papers, select a frontdesk officer and scanning due date in the counseling panel. Saving creates a scanning task and notification. Frontdesk scans the papers, opens the visitor profile, uploads the files, and completes the task. Counselors review document status. Student accounts are not required.

## Follow-up history

Every conversation is a separate follow-up. Complete it with an outcome, then schedule the next conversation. Completed entries cannot be reopened or overwritten. Select **Show all follow-up history** in Follow-ups to see past and current records, including completed dates, times, instructions and outcomes in Nepal time. Rescheduling retains previous dates and notes in the activity timeline and audit history.


## Visitor form configuration

In QR Studio, each saved registration QR has **Edit / preview / print form**.
Edit the title and introduction, show or hide reference fields, and mark them required.
Full name, mobile number and consent cannot be disabled. Address and highest education
are required by default; supporting details are optional. Save applies the settings
to the permanent QR link. Preview shows unsaved settings without submitting answers.
Print opens a blank paper version; choose a printer or Save as PDF in the print dialog.
Paper copies include consent, signature and date lines. Staff still verify the number
by OTP after entering paper answers.

Profiles now open in a separate tab at `/people/<person-id>`. Reloading or bookmarking
that page keeps the same profile. Staff authentication and existing access scopes apply.


## Shared student list and notifications

Every active staff role can view the complete people list and open profiles directly,
including students assigned to another counselor or branch. Profile access does not
require an access request. Assignment, editing, counselling, conversion and finance
permissions still follow each role's existing ownership and branch rules.

New unread notifications appear at the bottom right of the workspace and profile
pages. Staff can open the linked profile, mark the notification read, or dismiss
its pop-up. Dismissing keeps the notification in Notifications. Checks run every
15 seconds while the tab is visible; no operating-system push notification is sent.


## Study suggestion history

Open a student's profile, then Counseling → Suggested study options → Add suggestion.
Record the country, level, university, course and intake. Use Add another university /
course to save several options from one discussion together. Every option is a separate
history entry with the counselor, Nepal date/time and notes. Adding later options does
not replace earlier recommendations. Staff can read the history; existing counselling
permissions control who can add suggestions.


University, course and intake fields have searchable dropdowns. Type a name to search;
choose an existing option or click Add “name” when it is missing. New options are shared
with other counselors. Country/level and recommendation details remain stored in each
student's historical suggestion even when more options are added later.
