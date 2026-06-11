# Assignment Eligibility Engine — How It Works

## The Big Picture

Every week, Marriott has a list of **shifts** that need to be staffed by Instawork workers.  
The pipeline's job is to look at each shift and decide: **what action does ops need to take in Simplify?**

There are 4 possible outcomes for each shift:

| Outcome | What it means | Ops action |
|---|---|---|
| **ASSIGNED** | Worker already has a valid open assignment covering this shift | ✅ Nothing — done |
| **JOB REQUEST** | Worker is in Simplify but needs a new job posting for this shift | Create a job posting |
| **CAN UPLOAD** | Worker doesn't have a Candidate ID in Simplify yet | Create a candidate record first |
| **REVIEW** | Something unusual — mismatch, closed assignment, cross-site worker | Ops must manually investigate |

---

## Data Sources Used

| Source | What it contains |
|---|---|
| **Mode CSV** (raw data tab) | All shifts from Instawork — worker ID, site, dept, rate, shift date |
| **Open Active** (Simplify) | All currently open assignments in Simplify — who is assigned where and when |
| **Open & Closed** (Simplify) | Full history of all assignments — used to look up Candidate IDs and Job IDs |

---

## Step-by-Step Decision Logic

### Step 1 — Build a "Perfect Match" key for each shift
For each shift in Mode, build a lookup key:
```
Perfect Match = Department Code + Worker ID + Partner Rate
```
Then look this up in Open Active. If a match is found **and the shift date falls within the assignment's start/end dates**, we have a **Perfect AID** (Assignment ID).

> Example: Worker W001 at dept IT001 at rate $25/hr → lookup finds AID-555 running May 30–Jun 19 that covers the shift date → **Perfect AID = AID-555** → ASSIGNED ✅

---

### Step 2 — If no Perfect AID, try a "2nd Best Match"
Build a fallback key:
```
2nd Best Match = Site Name + Worker ID + Partner Rate
```
Look this up in Open Active the same way. If a match is found, we have a **2nd Best AID**.

Then check: does the department in Mode match the department on that assignment?
- **Dept matches → OK** — the worker is covered, probably just a dept name formatting difference
- **Dept doesn't match → Not OK** — the existing assignment is for a different department; a new job posting is needed

---

### Step 3 — CAN ID Lookup
For every shift where no AID was found (Perfect or 2nd Best), check if the worker already has a **Candidate ID** in Simplify.

Lookup source: `Open & Closed` tab, column `Vendor Tracking ID 1` → `Candidate ID`

- **CAN ID found** → worker exists in Simplify, just needs a job posting
- **CAN ID missing** → worker doesn't exist in Simplify yet, needs to be created first

---

### Step 4 — Classify each shift into an action bucket

```
For each shift:

  ┌── Perfect AID found?
  │     YES → ASSIGNED (do nothing)
  │
  │     NO ──┬── 2nd Best AID found?
  │          │     YES ──┬── Dept validation OK?
  │          │          │     YES → treat as ASSIGNED (covered by existing AID)
  │          │          │     NO  → JOB REQUEST (wrong dept, need new job)
  │          │
  │          │     NO ──┬── CAN ID found?
  │                    │     YES → JOB REQUEST (worker in Simplify, no open job for this shift)
  │                    │     NO  → CAN UPLOAD (worker not in Simplify at all)
```

---

## The "Final Assignment" Tab

This tab shows every Mode shift with its final determined status in one place — a consolidated action list for ops. Columns:

| Column | What it is |
|---|---|
| Worker Name | First + Last name from Mode |
| Worker ID | Instawork worker ID |
| Site | Property code (e.g. BWIAT) |
| Location | Full property name |
| Dept | Department code |
| Shift Date | Date of the shift |
| Partner Rate | Pay rate |
| Action | ASSIGNED / JOB REQUEST / CAN UPLOAD / REVIEW |
| Reason | Short explanation of why this action was chosen |
| CAN ID | Simplify Candidate ID (if exists) |
| Perfect AID | Direct assignment ID (if found) |
| 2nd Best AID | Fallback assignment ID (if found) |
| Should Review | Yes/No — flagged rows need manual ops attention |

---

## Special Cases Explained

### "Provisional Match"
A Perfect AID was found, but the department code was guessed/overridden because the exact code wasn't in our lookup table. The ops team should verify the dept code with the property contact before the first assignment is submitted.

### "Amend Review"
A 2nd Best AID was found, and the department *numeric code* actually matches — but the display name in Simplify is slightly different (e.g. "IT - Technology" vs "Information Technology"). This is a cosmetic mismatch, not a real error. These rows are also included in Job Request as standard ops procedure.

### "Should be reviewed = Yes" rows (highlighted red)
These rows have something unusual:
- Worker has an assignment but it's **closed or cancelled** for the shift period
- Worker is **assigned at a different site** than expected
- Dept code or rate is ambiguous / couldn't be confirmed

Ops should review these before acting on them.

---

## Output Tabs Summary

| Tab | Contents | Who uses it |
|---|---|---|
| **Final Assignment** | All shifts with action status — the master ops view | Ops lead review |
| **upload** | Ready-to-import direct assignments (needs Available Jobs filled in) | Ops import to Simplify |
| **job request** | Shifts needing a new Simplify job posting | Ops creates job postings |
| **can upload** | Workers needing a Candidate record created in Simplify | Ops creates candidates |
| **can output** | CAN Upload + blank Candidate ID column (filled after Simplify import) | Post-import tracking |
| **amend review** | Diagnostic: 2nd Best AID + dept name mismatch (also in Job Request) | Reference only |
| **provisional match** | Shifts where dept code was guessed — needs verification | Ops verify with property |
| **Output** | Empty template for tracking assignments after Simplify import | Post-import |
| **Sheet8** | Empty template for sensitive candidate data | Ops fill manually |
| **Summary** | Scratch/QA notes | Ops notes |
