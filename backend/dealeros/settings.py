"""Django settings for the DealerOS reconciliation slice."""
import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = BASE_DIR.parent

load_dotenv(REPO_ROOT / ".env")

SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "dev-only-not-a-secret")
DEBUG = os.environ.get("DJANGO_DEBUG", "1") == "1"
ALLOWED_HOSTS = ["*"]

INSTALLED_APPS = [
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.staticfiles",
    "rest_framework",
    "rest_framework.authtoken",
    "corsheaders",
    "tenancy",
    "reconciliation",
    "api",
    "grounded",
]

MIDDLEWARE = [
    "corsheaders.middleware.CorsMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    # Must sit after AuthenticationMiddleware: it needs to know who is asking
    # before it can pin the database session to an org.
    "tenancy.middleware.OrgContextMiddleware",
]

ROOT_URLCONF = "dealeros.urls"
WSGI_APPLICATION = "dealeros.wsgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
            ]
        },
    }
]

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.environ.get("POSTGRES_DB", "dealeros"),
        "USER": os.environ.get("POSTGRES_USER", "dealeros_app"),
        "PASSWORD": os.environ.get("POSTGRES_PASSWORD", "dealeros_app"),
        "HOST": os.environ.get("POSTGRES_HOST", "127.0.0.1"),
        "PORT": os.environ.get("POSTGRES_PORT", "5432"),
        "TEST": {"NAME": os.environ.get("POSTGRES_TEST_DB", "test_dealeros")},
    }
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
AUTH_PASSWORD_VALIDATORS = []
LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = False
USE_TZ = True
STATIC_URL = "static/"

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework.authentication.TokenAuthentication",
        "rest_framework.authentication.SessionAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.IsAuthenticated"],
    "UNAUTHENTICATED_USER": None,
    "DEFAULT_RENDERER_CLASSES": ["rest_framework.renderers.JSONRenderer"],
}

CORS_ALLOW_ALL_ORIGINS = DEBUG
CORS_ALLOW_HEADERS = ["authorization", "content-type"]

# Where the three source CSVs live.
DATA_DIR = Path(os.environ.get("DEALEROS_DATA_DIR", REPO_ROOT / "data"))

# Tables that hold tenant-owned rows. Every one of them must end up with
# row level security FORCED. `tests/test_tenant_isolation.py` asserts this by
# introspecting the live catalog, so adding a tenant table without adding it
# here fails the build.
TENANT_TABLES = [
    "tenancy_location",
    "reconciliation_sourcerecorda",
    "reconciliation_sourceentryb",
    "reconciliation_exception",
    "reconciliation_matchnote",
]

# Break-glass switch for the walkthrough only. When set, the RLS migration
# refuses to install policies so you can watch the isolation tests fail.
DISABLE_RLS = os.environ.get("DEALEROS_DISABLE_RLS", "") == "1"

# --- Grounded answer endpoint --------------------------------------------
LLM_API_KEY = os.environ.get("LLM_API_KEY", "").strip()
LLM_BASE_URL = os.environ.get("LLM_BASE_URL", "https://api.openai.com/v1").strip()
LLM_MODEL = os.environ.get("LLM_MODEL", "gpt-4o-mini").strip()
LLM_TIMEOUT_SECONDS = float(os.environ.get("LLM_TIMEOUT_SECONDS", "20"))

DEMO_PASSWORD = os.environ.get("DEMO_PASSWORD", "demo-password")

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "root": {"handlers": ["console"], "level": "INFO"},
}
