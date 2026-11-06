from django.core.management.base import BaseCommand, CommandError
from django.urls import reverse

from apps.accounts import services
from apps.accounts.models import User


class Command(BaseCommand):
    help = (
        "Create (or reactivate) a Customer Wise staff user and print a one-time sign-in link. "
        "Use this for the first staff account; there are no passwords (D02)."
    )

    def add_arguments(self, parser):
        parser.add_argument("email")
        parser.add_argument("--name", default="", help="Full name")

    def handle(self, *args, email, name, **options):
        email = email.strip().lower()
        if "@" not in email:
            raise CommandError("Enter a valid email address.")
        user = User.objects.filter(email__iexact=email).first()
        if user is None:
            user = User.objects.create_staff(email, full_name=name)
            self.stdout.write(f"Created staff user {email}.")
        else:
            if user.role != User.Role.STAFF or not user.is_active:
                user.role, user.is_active = User.Role.STAFF, True
                user.save(update_fields=["role", "is_active"])
                self.stdout.write(f"Made {email} an active staff user.")
            else:
                self.stdout.write(f"{email} is already an active staff user.")
        secret = services.create_login_token(user)
        link = services.absolute_url(reverse("accounts:magic_link", args=[secret]))
        self.stdout.write(self.style.SUCCESS("One-time sign-in link (expires in 15 minutes):"))
        self.stdout.write(link)
