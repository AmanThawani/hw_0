"""
views.py — HTTP request handlers for AttendanceChimp.

Each view corresponds to one URL endpoint.  Views receive an HTTP request,
interact with the database via models.py, and return an HTTP response.
"""

from django.shortcuts import render, redirect
from django.http import (HttpResponse, HttpResponseForbidden,
                         HttpResponseBadRequest, JsonResponse)
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST
from django.contrib.auth.models import User
from django.contrib.auth import authenticate, login
from datetime import datetime
import zoneinfo
import json
import io

from .models import InstructorProfile, StudentProfile

# from .models import (InstructorProfile, StudentProfile, Course, Enrollment,
#                      Lecture, QRCodeUpload, is_instructor, is_student,
#                      getUploadsForCourse)

CDT = zoneinfo.ZoneInfo("America/Chicago")


# ---------------------------------------------------------------------------
# HW2  — index, time, sum
# ---------------------------------------------------------------------------

def index(request):
    """Render the home page (index.html)."""
    current_time = datetime.now(CDT).strftime("%H:%M")
    context = {"current_time": current_time, "user": request.user}
    return render(request, 'app/index.html', context)


def time_view(request):
    """Return the current Central time as plain text, e.g. "13:24"."""
    now = datetime.now(CDT)
    return HttpResponse(now.strftime("%H:%M"))


def sum_view(request):
    """Return <p>n1 + n2</p> for /app/sum?n1=...&n2=...

    The autograder parses the response as HTML and reads the number in the <p> tag.
    """
    n1 = float(request.GET["n1"])
    n2 = float(request.GET["n2"])
    return HttpResponse("<p>" + str(n1 + n2) + "</p>")


# ---------------------------------------------------------------------------
# HW4  — user creation
# ---------------------------------------------------------------------------


@require_GET
def new(request):
    """Render the new.html page."""
    return render(request, 'app/new.html')


@csrf_exempt
@require_POST
def createUser(request):
    """Create a user account and log them in."""
    user_name = request.POST.get("user_name", "").strip()
    email = request.POST.get("email", "").strip()
    password = request.POST.get("password", "")
    is_student = request.POST.get("is_student", "")

    # validating all the required fields
    if not user_name or not email or not password or is_student not in ("1", "0"):
        return HttpResponseBadRequest("Missing or invalid fields")

    # Reject duplicate usernames or emails.
    if User.objects.filter(username=user_name).exists():
        return HttpResponse("Username already exists", status=409)

    if User.objects.filter(email=email).exists():
        return HttpResponse("Email already exists", status=409)

    # django user
    user = User.objects.create_user(
        username=user_name,
        email=email,
        password=password
    )

    # create corresponding profile
    if is_student == "1":
        StudentProfile.objects.create(user=user)
    else:
        InstructorProfile.objects.create(user=user)

    # log-in the newly created user
    login(request, user)

    return HttpResponse("User created successfully", status=200)


# ---------------------------------------------------------------------------
# HW5  — courses, lectures, QR uploads, dump
# ---------------------------------------------------------------------------



# ---------------------------------------------------------------------------
# HW6  — getUploads
# ---------------------------------------------------------------------------



