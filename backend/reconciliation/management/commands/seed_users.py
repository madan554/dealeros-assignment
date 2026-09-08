"""Creates one demo user per org.

The brief says authentication depth is not being tested and a hardcoded pair
of users is fine, so this is a management command rather than a signup flow.
"""
from django.conf import settings
from django.contrib.auth.models import User
from django.core.management.base import BaseCommand
from rest_framework.authtoken.models import Token

from tenancy.models import Org, OrgMembership

DEMO_USERS = [("alice", "ORG-A"), ("bob", "ORG-B")]


class Command(BaseCommand):
    help = "Create the demo users, one per org, and print their API tokens."

    def handle(self, *args, **options):
        password = settings.DEMO_PASSWORD
        for username, org_id in DEMO_USERS:
            org, _ = Org.objects.get_or_create(id=org_id, defaults={"name": org_id})
            user, _ = User.objects.get_or_create(
                username=username, defaults={"email": f"{username}@example.com"}
            )
            user.set_password(password)
            user.save()
            OrgMembership.objects.update_or_create(user=user, defaults={"org": org})
            token, _ = Token.objects.get_or_create(user=user)
            self.stdout.write(
                self.style.SUCCESS(f"{username:6} {org_id:6} password={password} token={token.key}")
            )
