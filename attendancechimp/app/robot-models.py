# =============================================================================
# robot-models.py -- LLM-generated data model for comparison (HW3, Part 5)
#
# LLM:  GPT-6 (OpenAI), via a fresh chat session, 2026-10-23
#
# Prompt (verbatim):
#
#   i'm going to give you a coding task, please write the code with docstrings.
#
#   i want you to write a Django models file called robot-models.py that works
#   for an app called AttendanceChimp. AttendanceChimp tracks class attendance
#   via QR codes. there are two roles for users: students and instructors. each
#   course has one instructor, but students can enroll in multiple courses.
#   there are also lectures (meaning specific class meetings), each of which is
#   associated with a course. lastly, we have individual QR code uploads, each
#   of which is a photo uploaded by a student. each one is associated with a
#   student and a lecture.
#
#   there should be tables for the following things, containing the following
#   attributes: InstructorProfile/StudentProfile (instead of one Users table),
#   Course (including at least name, instructor, meeting schedule), Enrollment
#   (serves to students to courses , which is many-to-many), Lecture (which is
#   linked to a course and has a date and a QR identifier), QRCodeUpload (whic
#   his linked to a student and [optionally] a lecture).
#
#   The app is intended to be simple and easy to use, support data analytics,
#   and be robust to cheating. i want you to prioritize  simplicity and
#   ease-of-use, since this is the crux of the specification.
#
# The code below is the model's output, unedited.
# =============================================================================

"""Database models for the AttendanceChimp attendance-tracking app.

Django's automatic model discovery expects these definitions to live in
``models.py``. This file uses the requested ``robot-models.py`` filename for
reference; copy the same contents to the app's ``models.py`` to have Django
load the models automatically.
"""

import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q


class InstructorProfile(models.Model):
    """Role-specific profile for an instructor.

    Authentication credentials remain in Django's configured user model, while
    this table stores the app-specific instructor role and its relationships.
    """

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="instructor_profile",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        """Order profiles by their related account's username by default."""

        ordering = ("created_at",)

    def __str__(self):
        """Return the instructor's full name, falling back to the user label."""
        get_full_name = getattr(self.user, "get_full_name", None)
        full_name = get_full_name() if callable(get_full_name) else ""
        return full_name or str(self.user)


class StudentProfile(models.Model):
    """Role-specific profile for a student."""

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="student_profile",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        """Order profiles by their related account's username by default."""

        ordering = ("created_at",)

    def __str__(self):
        """Return the student's full name, falling back to the user label."""
        get_full_name = getattr(self.user, "get_full_name", None)
        full_name = get_full_name() if callable(get_full_name) else ""
        return full_name or str(self.user)


class Course(models.Model):
    """A course taught by exactly one instructor profile."""

    name = models.CharField(max_length=200)
    instructor = models.ForeignKey(
        InstructorProfile,
        on_delete=models.PROTECT,
        related_name="courses",
    )
    meeting_schedule = models.CharField(
        max_length=255,
        help_text=(
            "Human-readable schedule, e.g. 'Mon/Wed 09:30-10:45'. "
            "Keep this format consistent if you plan to analyze it later."
        ),
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        """Display courses alphabetically by name."""

        ordering = ("name",)

    def __str__(self):
        """Return the course name for admin and form displays."""
        return self.name


class Enrollment(models.Model):
    """Join table recording a student's enrollment in a course."""

    student = models.ForeignKey(
        StudentProfile,
        on_delete=models.CASCADE,
        related_name="enrollments",
    )
    course = models.ForeignKey(
        Course,
        on_delete=models.CASCADE,
        related_name="enrollments",
    )
    enrolled_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        """Prevent duplicate student-course enrollments."""

        ordering = ("-enrolled_at",)
        constraints = [
            models.UniqueConstraint(
                fields=("student", "course"),
                name="uniq_ac_enroll_stud_course",
            ),
        ]

    def __str__(self):
        """Describe the enrollment in a readable way."""
        return f"{self.student} — {self.course}"


class Lecture(models.Model):
    """A single scheduled meeting of a course, with a unique QR identifier."""

    course = models.ForeignKey(
        Course,
        on_delete=models.CASCADE,
        related_name="lectures",
    )
    date = models.DateField(db_index=True)
    qr_identifier = models.UUIDField(
        default=uuid.uuid4,
        unique=True,
        editable=False,
        help_text=(
            "Random per-lecture identifier to encode in the QR code. "
            "The server should still enforce the valid check-in time window."
        ),
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        """List lectures chronologically, with course name as a secondary key."""

        ordering = ("date", "course__name")

    def __str__(self):
        """Return the course and meeting date."""
        return f"{self.course} — {self.date:%Y-%m-%d}"


class QRCodeUpload(models.Model):
    """A student's uploaded photo for attendance at a lecture.

    The lecture is optional so that an upload can be retained for review when
    the app cannot confidently associate it with a particular lecture.
    """

    student = models.ForeignKey(
        StudentProfile,
        on_delete=models.CASCADE,
        related_name="qr_uploads",
    )
    lecture = models.ForeignKey(
        Lecture,
        on_delete=models.SET_NULL,
        related_name="qr_uploads",
        null=True,
        blank=True,
    )
    photo = models.ImageField(
        upload_to="attendancechimp/qr_uploads/%Y/%m/%d/",
        help_text="Photo uploaded by the student for attendance verification.",
    )
    uploaded_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        """Keep uploads recent-first and allow at most one per student per lecture."""

        ordering = ("-uploaded_at",)
        constraints = [
            models.UniqueConstraint(
                fields=("student", "lecture"),
                condition=Q(lecture__isnull=False),
                name="uniq_ac_upload_stud_lecture",
            ),
        ]

    def clean(self):
        """Reject lecture assignments for courses where the student is not enrolled.

        Django forms and the admin call model validation automatically. Code
        that creates uploads directly should call ``full_clean()`` before save,
        or enforce the same enrollment check in its service/view layer.
        """
        super().clean()

        if not self.student_id or not self.lecture_id:
            return

        is_enrolled = Enrollment.objects.filter(
            student_id=self.student_id,
            course_id=self.lecture.course_id,
        ).exists()
        if not is_enrolled:
            raise ValidationError(
                {"lecture": "The student must be enrolled in this lecture's course."}
            )

    def __str__(self):
        """Identify the student and, when known, the associated lecture."""
        if self.lecture_id:
            return f"Upload by {self.student} for {self.lecture}"
        return f"Unassigned upload by {self.student}"
