# Decisions

Each entry: the decision, the alternative I rejected, and the line that
separated them.

---

### 1. The tenant boundary is Postgres row level security, and the application has no org filter at all

**Rejected:** a `TenantScopedManager` that adds `.filter(org=...)` to every
queryset, or a base viewset that applies the filter.

**Why:** the brief asks for enforcement somewhere a future teammate cannot
forget, and a manager is still application code — one `.objects.raw()`, one
`Model.objects.all()` in a management command, one aggregate written in a
hurry, and it is gone. I also chose not to keep a queryset filter *as well*:
two copies of a rule drift, and when they drift the weaker one is the one
people trust because it is the one they can see. So `api/views.py` genuinely
says `Exception.objects.all()`, and the database is what makes that mean "this
org's exceptions".

The cost is real and I would say it out loud in a code review: someone reading
the view cannot see the boundary. I paid for that with
`tests/test_tenant_isolation.py`, `manage.py rls_status`, and a comment at the
top of `views.py`.

---

### 2. `FORCE ROW LEVEL SECURITY`, and an application role that is neither superuser nor `BYPASSRLS`

**Rejected:** `ENABLE ROW LEVEL SECURITY` alone, connecting as the default
local Postgres superuser.

**Why:** both of those failure modes are completely silent. Postgres exempts a
table's owner from its own policies unless RLS is *forced*, and Django owns
these tables — so without `FORCE` the policy exists, reads correctly in
`pg_policy`, and does nothing. Superuser and `BYPASSRLS` skip policies
entirely. All three would leave a repo that looks like it has tenant isolation
and does not have any. `scripts/bootstrap_db.sh` creates a `NOSUPERUSER
NOBYPASSRLS` role, and there is a test for each of these specifically because
the natural fix for a local permissions error is to grant more privileges.

---

### 3. The ingest walks orgs one at a time instead of using a bypass role

**Rejected:** a second database role with `BYPASSRLS` for the loader, which is
the conventional answer for a job that writes across tenants.

**Why:** it would put a privileged connection in the repo, and privileged
connections get reused. Six months later something that only needed to read
one org is using the loader's credentials because they were there. Batching
the writes per org is maybe thirty extra lines in `ingest.py` and it means
there is no bypass path in the codebase for anyone to reach for. It also
exercises the boundary on the write side, which turned out to matter: the
policy's `WITH CHECK` clause has its own tests now.

---

### 4. Org ownership follows System A's location; a row that cannot be attributed goes to nobody

**Rejected:** attributing a row to whichever org either system points at, or
showing a cross-org row to both parties since both are arguably involved.

**Why:** REC-1077 is the case that forces this. System A puts it at LOC-102
(ORG-A) and System B puts it at LOC-201 (ORG-B), so the two systems disagree
about which *tenant* it belongs to. Showing it to both would be a leak
justified as helpfulness, and it is exactly the leak an org filter on a
queryset would never catch, because the row genuinely mentions both tenants.
System A wins because that is where the record exists at all — System B's row
is a reference to it. ORG-A sees it with the cross-boundary fact spelled out;
ORG-B never learns it exists. Following from the same rule, a location that is
not in `locations.csv` cannot be attributed, so those rows go to an operator
report rather than being guessed into a plausible tenant's list.

---

### 5. Normalise the format first, then compare; never guess a reference

**Rejected:** treating `rec1034`, `" REC - 1070 "`, `1112` and `1,25,400.00`
as data-quality exceptions in their own right.

**Why:** an exceptions list is only useful if every row needs a human. Those
four are damaged formatting that resolves to exactly one answer, so fixing
them is our job, not the reader's. They are recorded as notes and surfaced on
`/api/summary` so the work is auditable rather than invisible. The limit is
strict, though: anything that does not reduce to exactly one record number
stays unmatched rather than being guessed at, because a wrong match can file a
row under the wrong tenant, and that is worse than an unresolved row.

Note `1,25,400.00` is both things at once. The format was normalised; the
number that survived (125,400.00 against System A's 183,244.16) is a real
disagreement. The exception is about the money, not the punctuation.

---

### 6. Split a reason code when it names a cause; suppress one when it adds noise

**Rejected:** one code per kind of field difference — amounts, dates,
locations — applied uniformly.

**Why:** the list is only worth reading if every row earns a human's
attention, and that cuts both ways.

Splitting: three records (REC-1003, REC-1027, REC-1088) differ from System A
by *exactly* the adjustment, because System B recorded `base_value` where it
should have recorded `total_value`. `AMOUNT_MISMATCH` would be true and would
send someone to check three records one at a time.
`ADJUSTMENT_MISSING_IN_SYSTEM_B` tells them one export dropped a column: one
fix, not three investigations. REC-1064 is the control — its amount matches
neither the base nor the total, so it stays a plain mismatch.

Suppressing: REC-1019 is voided in System A while System B still holds an
entry. Once that is true, the amount and the date describe an entry that
should not exist, and three rows on one record push the actionable one down
the list. Same reasoning inverted, a voided record with *no* System B entry is
the systems working correctly, so it is a note rather than a gap.

---

### 7. The language model writes a query plan; it never sees data and never produces a number

**Rejected:** giving a model the exception rows and asking it to answer, with
a prompt instructing it to cite identifiers.

**Why:** the brief asks whether the endpoint can be made to lie, and the
honest answer for "model reads rows, model writes prose" is yes, eventually.
So the model gets one job: turn a question into JSON naming fields from
`grounded/schema.py`. That plan is validated field by field and value by value
against a vocabulary built from the caller's own org, Postgres computes every
figure, and the answer sentence is assembled from templates in
`grounded/service.py`. The model never receives a single exception row, so it
has nothing to paraphrase and no number to get wrong. The ceiling on a
successful prompt injection is a differently-shaped query or a refusal, and
there is a test that hands the planner an injected answer and total and shows
neither reaches the response.

---

### 8. A question about another org is refused, not answered with zero

**Rejected:** letting row level security handle it, which would return no rows
and answer "0 exceptions".

**Why:** "0" is a factual claim about the other org's data, and it would be
wrong. A confidently wrong zero is precisely the failure this endpoint exists
to prevent, and it is worse than a refusal because it looks like an answer.
The guard runs on the question text before anything else, and the plan
validator catches it again from the other side, since the vocabulary of
allowed location ids is built per-caller. Both directions are tested: the
first version of the guard let "organisation B" through.

---

### 9. The deterministic keyword planner is the default; the LLM is opt-in

**Rejected:** requiring an API key, or silently falling back to keywords when
the model is unreachable.

**Why:** two reasons. The repo has to work from a clean clone without a key,
and a test suite whose assertions depend on a remote model is not a test
suite. So the keyword planner ships as the default and is what the tests run
against; `LLM_API_KEY` switches in the model for a much wider range of
phrasings. It does *not* fall back: if the model is unreachable the endpoint
refuses with `PLANNER_UNAVAILABLE`, because silently downgrading would answer
a question the user believes a model understood. The response always names
which planner ran.

---

### 10. One exception row per (record, reason), not per record

**Rejected:** one row per record with a list of problems.

**Why:** the brief asks for filtering by reason code, and a row that carries
three reasons cannot be filtered by one of them without either appearing under
a reason that is not its main problem or disappearing from a filter it should
match. It also keeps "what do I do about this" singular per row. The supplied
dataset happens to have at most one reason per record, so this is a bet on the
next export rather than this one; `test_one_record_can_carry_more_than_one_reason`
pins the behaviour.
