"""Prints what the live Postgres catalog says about the tenant boundary.

Used in the walkthrough. Reads pg_class and pg_policy rather than trusting our
own migrations, because the point of the exercise is that the guarantee is in
the database.
"""
from django.core.management.base import BaseCommand

from tenancy.rls import connection_privileges, rls_status


class Command(BaseCommand):
    help = "Report row level security state for every tenant table."

    def handle(self, *args, **options):
        privileges = connection_privileges()
        self.stdout.write(f"Connected as role: {privileges['role']}")
        for label, key in (("superuser", "is_superuser"), ("bypasses RLS", "bypasses_rls")):
            value = privileges[key]
            style = self.style.ERROR if value else self.style.SUCCESS
            self.stdout.write(f"  {label:14} {style(str(value))}")
        if privileges["is_superuser"] or privileges["bypasses_rls"]:
            self.stdout.write(
                self.style.ERROR(
                    "  This role ignores row level security. The tenant boundary is not "
                    "being enforced. See scripts/bootstrap_db.sh."
                )
            )

        self.stdout.write("")
        self.stdout.write(f"{'table':38} {'enabled':>8} {'forced':>7} {'policies':>9}")
        all_good = True
        for table, state in rls_status().items():
            if state is None:
                self.stdout.write(self.style.ERROR(f"{table:38} {'MISSING TABLE':>26}"))
                all_good = False
                continue
            good = state["enabled"] and state["forced"] and state["policies"] > 0
            all_good = all_good and good
            line = (
                f"{table:38} {str(state['enabled']):>8} {str(state['forced']):>7} "
                f"{state['policies']:>9}"
            )
            self.stdout.write(self.style.SUCCESS(line) if good else self.style.ERROR(line))

        self.stdout.write("")
        if all_good and not privileges["is_superuser"] and not privileges["bypasses_rls"]:
            self.stdout.write(self.style.SUCCESS("Tenant boundary is enforced by the database."))
        else:
            self.stdout.write(
                self.style.ERROR("Tenant boundary is NOT fully enforced by the database.")
            )
