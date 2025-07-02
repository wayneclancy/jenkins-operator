import os
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

SECRET_KEY = "replace-me"
DEBUG = True
ALLOWED_HOSTS = ["*"]

INSTALLED_APPS = [
    "django.contrib.staticfiles",
    "jenkinsui",
]

ROOT_URLCONF = "jenkinsui.urls"
TEMPLATES = [{
    "BACKEND": "django.template.backends.django.DjangoTemplates",
    "DIRS": [os.path.join(BASE_DIR, "templates")],
    "APP_DIRS": True,
    "OPTIONS": {
        "context_processors": [
            "django.template.context_processors.debug",
        ],
    },
}]

WSGI_APPLICATION = "jenkinsui.wsgi.application"
STATIC_URL = "/static/"
