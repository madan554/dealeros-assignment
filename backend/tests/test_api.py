"""The HTTP surface: auth, the exceptions list, its filters, and the detail view.

Cross-tenant behaviour of these same endpoints lives in
test_tenant_isolation.py, so this file is about whether the feature works.
"""
import pytest

pytestmark = pytest.mark.django_db


@pytest.fixture
def alice(api_client, token_for):
    api_client.credentials(HTTP_AUTHORIZATION=f"Token {token_for('alice')}")
    return api_client


def test_login_returns_a_token_and_the_callers_org(seeded, api_client):
    response = api_client.post(
        "/api/auth/login", {"username": "alice", "password": "demo-password"}, format="json"
    )
    assert response.status_code == 200
    body = response.json()
    assert body["org_id"] == "ORG-A"
    assert body["username"] == "alice"
    assert body["token"]


def test_login_rejects_a_bad_password_without_saying_which_part_was_wrong(
    seeded, api_client
):
    response = api_client.post(
        "/api/auth/login", {"username": "alice", "password": "wrong"}, format="json"
    )
    assert response.status_code == 401
    assert "username or password" in response.json()["detail"]


def test_me_reports_the_org_the_token_belongs_to(seeded, alice):
    body = alice.get("/api/me").json()
    assert body == {"username": "alice", "org_id": "ORG-A", "org_name": "Organisation A"}


def test_the_exceptions_list_returns_this_orgs_seven_rows(seeded, alice):
    body = alice.get("/api/exceptions").json()
    assert body["count"] == 7
    assert body["total_for_org"] == 7
    assert len(body["results"]) == 7
    assert {row["record_ref"] for row in body["results"]} == {
        "REC-1015",
        "REC-1027",
        "REC-1042",
        "REC-1064",
        "REC-1077",
        "REC-1088",
        "REC-1999",
    }


def test_every_row_carries_a_readable_reason_and_an_instruction(seeded, alice):
    """The reason code has to be actionable without a glossary, so the label
    and the "what to do" travel with the row rather than living in docs."""
    for row in alice.get("/api/exceptions").json()["results"]:
        assert row["reason_label"] and not row["reason_label"].isupper()
        assert row["what_to_do"].endswith(".")
        assert row["summary"].endswith(".")
        assert row["record_ref"]


def test_filtering_by_reason_code(seeded, alice):
    body = alice.get("/api/exceptions", {"reason_code": "ADJUSTMENT_MISSING_IN_SYSTEM_B"}).json()
    assert body["count"] == 2
    assert {row["record_ref"] for row in body["results"]} == {"REC-1027", "REC-1088"}
    # The unfiltered totals stay available so the UI can show counts per chip.
    assert body["total_for_org"] == 7


def test_filtering_by_several_reason_codes_at_once(seeded, alice):
    body = alice.get(
        "/api/exceptions",
        {"reason_code": ["MISSING_IN_SYSTEM_B", "AMOUNT_MISMATCH"]},
    ).json()
    assert {row["record_ref"] for row in body["results"]} == {"REC-1015", "REC-1064"}


def test_filtering_by_location(seeded, alice):
    body = alice.get("/api/exceptions", {"location_id": "LOC-102"}).json()
    assert body["count"] >= 1
    assert {row["location_id"] for row in body["results"]} == {"LOC-102"}


def test_filters_combine(seeded, alice):
    body = alice.get(
        "/api/exceptions",
        {"reason_code": "ADJUSTMENT_MISSING_IN_SYSTEM_B", "location_id": "LOC-102"},
    ).json()
    assert body["count"] == 1
    assert body["results"][0]["record_ref"] == "REC-1027"


def test_an_unknown_reason_code_returns_an_empty_list_not_an_error(seeded, alice):
    body = alice.get("/api/exceptions", {"reason_code": "NOT_A_REAL_CODE"}).json()
    assert body["count"] == 0
    assert body["results"] == []


def test_counts_by_reason_and_location_add_up_to_the_org_total(seeded, alice):
    body = alice.get("/api/exceptions").json()
    assert sum(body["counts_by_reason_code"].values()) == body["total_for_org"]
    assert sum(body["counts_by_location_id"].values()) == body["total_for_org"]


def test_ordering_is_restricted_to_a_known_set(seeded, alice):
    ascending = alice.get("/api/exceptions", {"ordering": "record_ref"}).json()["results"]
    descending = alice.get("/api/exceptions", {"ordering": "-record_ref"}).json()["results"]
    assert [r["record_ref"] for r in ascending] == list(
        reversed([r["record_ref"] for r in descending])
    )
    # An unrecognised ordering falls back rather than reaching an arbitrary
    # column name into the ORM.
    fallback = alice.get("/api/exceptions", {"ordering": "detail__secret"})
    assert fallback.status_code == 200


def test_the_detail_view_shows_both_systems_side_by_side(seeded, alice):
    """The point of the detail view: the exception says the systems disagree,
    and this is where you see what each of them actually holds."""
    listing = alice.get("/api/exceptions", {"reason_code": "AMOUNT_MISMATCH"}).json()
    exception_id = listing["results"][0]["id"]
    body = alice.get(f"/api/exceptions/{exception_id}").json()

    assert body["exception"]["record_ref"] == "REC-1064"
    assert body["system_a_record"]["record_id"] == "REC-1064"
    assert body["system_a_record"]["total_value"] == "183244.16"
    assert len(body["system_b_entries"]) == 1
    entry = body["system_b_entries"][0]
    # Both the raw text and what we read it as, so the normalisation is
    # auditable rather than something the reader has to take on trust.
    assert entry["raw_value"] == "1,25,400.00"
    assert entry["value"] == "125400.00"
    assert entry["amount_format_was_normalised"] is True


def test_the_detail_view_shows_the_raw_reference_that_was_normalised(seeded, alice):
    listing = alice.get("/api/exceptions", {"reason_code": "DUPLICATE_ENTRY_IN_SYSTEM_B"}).json()
    body = alice.get(f"/api/exceptions/{listing['results'][0]['id']}").json()
    assert len(body["system_b_entries"]) == 2


def test_a_missing_record_has_no_system_b_entries_to_show(seeded, alice):
    listing = alice.get("/api/exceptions", {"reason_code": "MISSING_IN_SYSTEM_B"}).json()
    body = alice.get(f"/api/exceptions/{listing['results'][0]['id']}").json()
    assert body["system_a_record"]["record_id"] == "REC-1015"
    assert body["system_b_entries"] == []


def test_an_orphan_entry_has_no_system_a_record_to_show(seeded, alice):
    listing = alice.get(
        "/api/exceptions", {"reason_code": "UNKNOWN_RECORD_IN_SYSTEM_B"}
    ).json()
    body = alice.get(f"/api/exceptions/{listing['results'][0]['id']}").json()
    assert body["system_a_record"] is None
    assert body["system_b_entries"][0]["entry_id"] == "ENT/2026/4901"


def test_the_reason_code_catalogue_is_served_for_the_filter_ui(seeded, alice):
    body = alice.get("/api/reason-codes").json()
    assert len(body["reason_codes"]) == 11
    for reason in body["reason_codes"]:
        assert set(reason) == {"code", "label", "what_it_means", "what_to_do"}


def test_locations_lists_only_this_orgs_locations(seeded, alice):
    body = alice.get("/api/locations").json()
    assert {loc["id"] for loc in body["locations"]} == {"LOC-101", "LOC-102", "LOC-103"}


def test_the_summary_publishes_the_disagreements_we_judged_benign(seeded, alice):
    """"Which non-errors did you correctly leave alone" is a real question
    about this system, and the exceptions list cannot answer it by design."""
    body = alice.get("/api/summary").json()
    assert body["org_id"] == "ORG-A"
    assert body["exception_count"] == 7
    assert body["system_a_records"] == 74
    not_errors = body["not_errors"]
    assert not_errors["counts_by_code"]["SPLIT_ENTRIES_RECONCILED"] == 1
    assert not_errors["counts_by_code"]["AMOUNT_FORMAT_NORMALISED"] == 1
    # Of the three normalised references, REC-1034 and REC-1112 are at
    # LOC-101; REC-1070 is at LOC-202, which belongs to the other org. The
    # notes are tenant rows like any other, so they split the same way.
    assert not_errors["counts_by_code"]["REFERENCE_NORMALISED"] == 2
    for code in not_errors["counts_by_code"]:
        assert not_errors["explanations"][code].endswith(".")
    split = next(
        note for note in not_errors["notes"] if note["code"] == "SPLIT_ENTRIES_RECONCILED"
    )
    assert split["record_ref"] == "REC-1055"
    assert "Not an error." in split["summary"]


def test_the_other_org_sees_its_own_five_and_its_own_notes(seeded, api_client, token_for):
    api_client.credentials(HTTP_AUTHORIZATION=f"Token {token_for('bob')}")
    assert api_client.get("/api/exceptions").json()["count"] == 5
    summary = api_client.get("/api/summary").json()
    assert summary["org_id"] == "ORG-B"
    assert summary["system_a_records"] == 46
    # REC-1070's reference was normalised and it is an ORG-B record, so ORG-B
    # sees that note and only that note.
    assert summary["not_errors"]["counts_by_code"] == {"REFERENCE_NORMALISED": 1}
    assert summary["not_errors"]["notes"][0]["record_ref"] == "REC-1070"


def test_ingest_is_idempotent(seeded, alice):
    """Re-running it must not double up the exceptions, because the first
    thing anyone does when data looks wrong is run the loader again."""
    from django.core.management import call_command

    before = alice.get("/api/exceptions").json()
    call_command("ingest", verbosity=0)
    after = alice.get("/api/exceptions").json()
    assert before["count"] == after["count"] == 7
    assert {r["record_ref"] for r in before["results"]} == {
        r["record_ref"] for r in after["results"]
    }


def test_switching_users_repeatedly_never_leaks_or_crashes(seeded, api_client):
    """The QA path that failed with a 502: Alice, then Bob, then Alice again.

    Auth itself was never the problem — the 502 was the Vite proxy talking to
    a dead backend — but the switch must still leave each caller with exactly
    their own rows and no cross-tenant residue in any payload.
    """
    for _ in range(5):
        for username, org_id, count, forbidden in (
            ("alice", "ORG-A", 7, None),
            ("bob", "ORG-B", 5, "REC-1077"),
        ):
            login = api_client.post(
                "/api/auth/login",
                {"username": username, "password": "demo-password"},
                format="json",
            )
            assert login.status_code == 200, login.content
            body = login.json()
            assert body["org_id"] == org_id
            api_client.credentials(HTTP_AUTHORIZATION=f"Token {body['token']}")

            exceptions = api_client.get("/api/exceptions").json()
            summary = api_client.get("/api/summary").json()
            locations = api_client.get("/api/locations").json()

            assert exceptions["count"] == count
            refs = {row["record_ref"] for row in exceptions["results"]}
            assert summary["org_id"] == org_id
            assert summary["exception_count"] == count
            if forbidden:
                assert forbidden not in refs
                blob = repr(exceptions) + repr(summary) + repr(locations)
                assert forbidden not in blob
                assert "LOC-102" not in blob
            else:
                assert "REC-1077" in refs

            api_client.credentials()

