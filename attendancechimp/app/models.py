"""
models.py — Data model for AttendanceChimp.

AttendanceChimp is an electronic class attendance tool.  Instructors create
courses and lectures; each lecture is assigned a random QR code.  Students
photograph the QR code and upload the image.  The system decodes the QR code,
matches it to a lecture, and records the attendance.

Entities
--------
- InstructorProfile : extends Django User → instructor-specific data
- StudentProfile  : extends Django User → student-specific data
- Course          : a course taught by one instructor
- Enrollment      : junction table linking students to courses
- Lecture         : a single class meeting (has a QR-code identifier)
- QRCodeUpload    : a student's uploaded QR-code photograph
"""

from django.core.exceptions import ValidationError
from django.db import models
from django.contrib.auth.models import User
from django.utils import timezone

import datetime
import hashlib
import logging
import secrets
import string
import random

logger = logging.getLogger(__name__) # set up tracking, auditing, and debugging
                                                               # allows using logger.warning(), logger.error(), 
                                                               # logger.info(), etc. Better than print() for debuggin... 


# ---------------------------------------------------------------------------
# User profiles
# ---------------------------------------------------------------------------
#
# Authentication (username, password, email, name) is handled by Django's
# built-in User table.  Role-specific data lives in two separate profile
# tables rather than one Users table with a "role" column, because:
#   * students and instructors store different attributes, so a single table
#     would leave half its columns empty on every row;
#   * foreign keys can then target the correct role -- Course.instructor
#     points at InstructorProfile and Enrollment.student at StudentProfile, so
#     the database itself prevents e.g. a student being recorded as the
#     instructor of a course;
#   * one User may hold BOTH profiles.  This is common for TAs, who are
#     students in some courses and teaching staff in others.

class StudentProfile(models.Model):
    """
    Student-specific data attached to a Django User.

    Fields
    ------
    user            : the login account (one-to-one; deleting the account
                      deletes the profile).
    student_id      : university ID number, unique across all students.
    graduation_year : expected graduation year.  Stored instead of a class
                      year ("2nd year") so it never needs updating and still
                      makes sense for alumni / past students.
    major           : primary major.
    second_major    : optional second major (blank if none).
    """
    user = models.OneToOneField(
        User, on_delete=models.CASCADE, related_name="student_profile"
    )
    student_id = models.CharField(max_length=20, unique=True)
    graduation_year = models.PositiveSmallIntegerField()
    major = models.CharField(max_length=100)
    second_major = models.CharField(max_length=100, blank=True)

    def __str__(self):
        return f"{self.user.get_full_name() or self.user.username} ({self.student_id})"


class InstructorProfile(models.Model):
    """
    Teaching-staff data attached to a Django User.

    Covers professors, lecturers and teaching assistants.  A TA who is also
    a student simply has both an InstructorProfile and a StudentProfile.

    Fields
    ------
    user       : the login account (one-to-one; deleting the account deletes
                 the profile).
    department : academic department, e.g. "Data Science".
    title      : one of the Title choices below.
    """

    class Title(models.TextChoices):
        PROFESSOR = "PROF", "Professor"
        LECTURER = "LECT", "Lecturer"
        TA = "TA", "Teaching Assistant"

    user = models.OneToOneField(
        User, on_delete=models.CASCADE, related_name="instructor_profile"
    )
    department = models.CharField(max_length=100)
    title = models.CharField(max_length=4, choices=Title.choices)

    def is_ta(self):
        """Return True if this staff member is a teaching assistant."""
        return self.title == self.Title.TA

    def __str__(self):
        name = self.user.get_full_name() or self.user.username
        return f"{name} ({self.get_title_display()})"




# ---------------------------------------------------------------------------
# Course
# ---------------------------------------------------------------------------

class Course(models.Model):
    """
    One offering of a course in a specific term, e.g. DATA 13600, Autumn 2026.

    Each offering is its own row, so attendance from different years never
    mixes; (code, term, year) is unique.

    Fields
    ------
    code         : catalog code, e.g. "DATA 13600".
    name         : course title, e.g. "Introduction to Data Engineering".
    term, year   : when this offering runs.
    instructor   : the single instructor of record.  on_delete=SET_NULL, so
                   deleting an instructor never deletes the course (and its
                   lectures and attendance records) -- CASCADE would destroy
                   history, and PROTECT would force reassigning past courses to
                   someone who did not actually teach them.  The preferred way
                   to retire an instructor is to deactivate their User
                   (is_active=False), which keeps their name on past courses.
    tas          : teaching assistants (any number).  Separate from
                   `instructor`, so the course still has exactly one
                   instructor of record.
    meeting_days : days the course meets, e.g. "MWF" or "TTh".
    start_time,
    end_time     : regular meeting time, used to judge whether an upload
                   happened during class.
    location     : room, e.g. "Ryerson 251" (optional).
    """

    class Term(models.TextChoices):
        AUTUMN = "AUT", "Autumn"
        WINTER = "WIN", "Winter"
        SPRING = "SPR", "Spring"
        SUMMER = "SUM", "Summer"

    code = models.CharField(max_length=20)
    name = models.CharField(max_length=200)
    term = models.CharField(max_length=3, choices=Term.choices)
    year = models.PositiveSmallIntegerField()

    instructor = models.ForeignKey(
        InstructorProfile,
        on_delete=models.SET_NULL,
        null=True,
        related_name="courses_taught",
    )
    tas = models.ManyToManyField(
        InstructorProfile, blank=True, related_name="courses_assisted"
    )

    meeting_days = models.CharField(max_length=10)
    start_time = models.TimeField()
    end_time = models.TimeField()
    location = models.CharField(max_length=100, blank=True)

    # Shortcut over the Enrollment table (no extra table is created):
    # course.students.all() / student.courses.all().
    students = models.ManyToManyField(
        StudentProfile, through="Enrollment", related_name="courses"
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["code", "term", "year"], name="unique_course_offering"
            ),
            models.CheckConstraint(
                condition=models.Q(end_time__gt=models.F("start_time")),
                name="course_ends_after_start",
            ),
        ]

    def __str__(self):
        return f"{self.code} {self.get_term_display()} {self.year}"




# ---------------------------------------------------------------------------
# Enrollment
# ---------------------------------------------------------------------------

class Enrollment(models.Model):
    """
    Junction table for the many-to-many relationship between students and
    courses.  One row = "student S is enrolled in course C".

    A student is enrolled in a given course at most once; (student, course)
    is unique.  Dropping a course changes `status` instead of deleting the
    row, so the student's attendance history stays linked to the course.

    Fields
    ------
    student     : the enrolled student (CASCADE: an enrollment is meaningless
                  without its student).
    course      : the course offering (CASCADE, same reasoning).
    enrolled_at : when the student joined, so analytics don't count a
                  student as absent from lectures held before they enrolled.
    status      : one of the Status choices below.  Analytics typically
                  filter to ACTIVE (and possibly AUDITING) enrollments.
    """

    class Status(models.TextChoices):
        ACTIVE = "ACTIVE", "Active"
        DROPPED = "DROPPED", "Dropped"
        WITHDRAWN = "WITHDRAWN", "Withdrawn"
        AUDITING = "AUDITING", "Auditing"

    student = models.ForeignKey(
        StudentProfile, on_delete=models.CASCADE, related_name="enrollments"
    )
    course = models.ForeignKey(
        Course, on_delete=models.CASCADE, related_name="enrollments"
    )
    enrolled_at = models.DateTimeField(default=timezone.now)
    status = models.CharField(
        max_length=10, choices=Status.choices, default=Status.ACTIVE
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["student", "course"], name="unique_enrollment"
            ),
        ]

    def __str__(self):
        return f"{self.student} in {self.course} ({self.get_status_display()})"




# ---------------------------------------------------------------------------
# Lecture
# ---------------------------------------------------------------------------

QR_CODE_LENGTH = 16
QR_CODE_ALPHABET = string.ascii_letters + string.digits  # 62 symbols


def generate_qr_code():
    """
    Return a random, unguessable QR identifier for a new Lecture.

    Uses the `secrets` module (cryptographically secure) rather than
    `random`, whose output is predictable to anyone who learns its state.
    16 symbols from a 62-letter alphabet gives 62**16 ~ 5e28 possible codes.
    """
    return "".join(secrets.choice(QR_CODE_ALPHABET) for _ in range(QR_CODE_LENGTH))


class Lecture(models.Model):
    """
    A single class meeting of a course: a lecture, discussion section, lab,
    exam, etc.  Each meeting gets its own random QR code, which students
    photograph and upload to record attendance.

    Fields
    ------
    course     : the course offering (CASCADE: a meeting is meaningless
                 without its course).
    kind       : one of the Kind choices below.
    led_by     : who runs this meeting, if not the course's instructor --
                 e.g. a TA leading a discussion section or lab.  Blank means
                 the instructor of record.  Must be the course's instructor
                 or one of its TAs (checked in clean()).
    date       : the day of the meeting.
    start_time,
    end_time   : when the meeting runs.  Copied from the course's regular
                 schedule if left blank, but can be overridden for a
                 rescheduled class, review session, etc.  Uploads are only
                 accepted inside this window (see accepts_upload_at()).
    qr_code    : random 16-character identifier, generated automatically
                 and unique across all lectures, so codes cannot be guessed
                 or reused from another meeting.
    created_at : when the record was created (audit trail).

    Note: Django calls clean() when saving through the admin site or a
    ModelForm, not on a bare save().
    """

    class Kind(models.TextChoices):
        LECTURE = "LECTURE", "Lecture"
        DISCUSSION = "DISCUSSION", "Discussion section"
        LAB = "LAB", "Lab"
        EXAM = "EXAM", "Exam"
        REVIEW = "REVIEW", "Review session"

    course = models.ForeignKey(
        Course, on_delete=models.CASCADE, related_name="lectures"
    )
    kind = models.CharField(
        max_length=10, choices=Kind.choices, default=Kind.LECTURE
    )
    led_by = models.ForeignKey(
        InstructorProfile,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="meetings_led",
    )
    date = models.DateField()
    start_time = models.TimeField(blank=True)
    end_time = models.TimeField(blank=True)
    qr_code = models.CharField(
        max_length=QR_CODE_LENGTH,
        unique=True,
        default=generate_qr_code,
        editable=False,
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["date", "start_time"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(end_time__gt=models.F("start_time")),
                name="lecture_ends_after_start",
            ),
        ]

    def clean(self):
        """Ensure led_by is the course's instructor or one of its TAs."""
        if self.led_by_id is None or self.course_id is None:
            return
        is_instructor = self.led_by_id == self.course.instructor_id
        is_ta = self.course.tas.filter(pk=self.led_by_id).exists()
        if not (is_instructor or is_ta):
            raise ValidationError(
                {"led_by": "Must be this course's instructor or one of its TAs."}
            )

    def save(self, *args, **kwargs):
        """Fill in start/end times from the course schedule if not given."""
        if self.start_time is None:
            self.start_time = self.course.start_time
        if self.end_time is None:
            self.end_time = self.course.end_time
        super().save(*args, **kwargs)

    def leader(self):
        """Return whoever runs this meeting: led_by, else the instructor."""
        return self.led_by or self.course.instructor

    def window(self):
        """Return (start, end) of this meeting as timezone-aware datetimes."""
        tz = timezone.get_current_timezone()
        start = datetime.datetime.combine(self.date, self.start_time, tzinfo=tz)
        end = datetime.datetime.combine(self.date, self.end_time, tzinfo=tz)
        return start, end

    def accepts_upload_at(self, when, grace_minutes=10):
        """
        Return True if an upload at time `when` falls inside this meeting,
        allowing `grace_minutes` on either side.
        """
        start, end = self.window()
        grace = datetime.timedelta(minutes=grace_minutes)
        return start - grace <= when <= end + grace

    def __str__(self):
        return f"{self.course} {self.get_kind_display()} on {self.date}"




# ---------------------------------------------------------------------------
# QR Code Upload
# ---------------------------------------------------------------------------

class QRCodeUpload(models.Model):
    """
    A photo of a lecture's QR code uploaded by a student.  An ACCEPTED
    upload is the record that the student attended that lecture.

    Every upload is kept, including rejected ones -- failed attempts are
    evidence (e.g. a student submitting Tuesday's code on Thursday).
    Decoding the image itself happens in the upload view; this model stores
    the decoded string and decides whether it counts (see process()).

    Anti-cheating measures
    ----------------------
    * uploaded_at is set by the server, never by the client, so uploads
      cannot be backdated.
    * the upload must match a real lecture's QR code, the student must be
      enrolled in that course, and it must arrive during the meeting.
    * at most one ACCEPTED upload per (student, lecture), enforced by a
      conditional unique constraint; rejected retries are still kept.
    * image_hash (SHA-256 of the file) exposes photos forwarded between
      students: the same bytes uploaded by two different students.

    Fields
    ------
    student      : who uploaded (CASCADE: if the student record is deleted,
                   their uploads no longer matter).
    lecture      : the lecture whose code was decoded.  Optional: empty when
                   the image was unreadable or the code matches no lecture.
                   CASCADE: deleting a lecture means its attendance is no
                   longer wanted.
    image        : the uploaded photo.  FileField rather than ImageField,
                   which would require the Pillow library.
    image_hash   : SHA-256 hex digest of the file, computed on save.
    decoded_code : the raw string read from the QR code, stored even if it
                   matches nothing.
    uploaded_at  : server timestamp of the upload.
    status       : outcome of processing; one of the Status choices below.
    """

    class Status(models.TextChoices):
        PENDING = "PENDING", "Pending"
        ACCEPTED = "ACCEPTED", "Accepted"
        UNREADABLE = "UNREADABLE", "Image unreadable"
        UNKNOWN_CODE = "UNKNOWN_CODE", "Code matches no lecture"
        NOT_ENROLLED = "NOT_ENROLLED", "Student not enrolled in course"
        OUTSIDE_WINDOW = "OUTSIDE_WINDOW", "Uploaded outside meeting time"
        DUPLICATE = "DUPLICATE", "Attendance already recorded"

    student = models.ForeignKey(
        StudentProfile, on_delete=models.CASCADE, related_name="uploads"
    )
    lecture = models.ForeignKey(
        Lecture,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="uploads",
    )
    image = models.FileField(upload_to="qr_uploads/%Y/%m/%d/")
    image_hash = models.CharField(max_length=64, blank=True, db_index=True)
    decoded_code = models.CharField(max_length=QR_CODE_LENGTH, blank=True)
    uploaded_at = models.DateTimeField(auto_now_add=True)
    status = models.CharField(
        max_length=14, choices=Status.choices, default=Status.PENDING
    )

    class Meta:
        ordering = ["-uploaded_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["student", "lecture"],
                condition=models.Q(status="ACCEPTED"),
                name="one_accepted_upload_per_lecture",
            ),
        ]

    def save(self, *args, **kwargs):
        """Compute image_hash from the file contents if not already set."""
        if self.image and not self.image_hash:
            digest = hashlib.sha256()
            for chunk in self.image.chunks():
                digest.update(chunk)
            self.image_hash = digest.hexdigest()
        super().save(*args, **kwargs)

    def process(self):
        """
        Match decoded_code to a lecture, run the attendance checks, set
        status (and lecture), and save.  Call after the upload is saved, so
        uploaded_at holds the server time.  Returns the new status.
        """
        self.lecture = Lecture.objects.filter(qr_code=self.decoded_code).first()

        if not self.decoded_code:
            self.status = self.Status.UNREADABLE
        elif self.lecture is None:
            self.status = self.Status.UNKNOWN_CODE
        elif not Enrollment.objects.filter(
            student=self.student,
            course=self.lecture.course,
            status__in=[Enrollment.Status.ACTIVE, Enrollment.Status.AUDITING],
        ).exists():
            self.status = self.Status.NOT_ENROLLED
        elif not self.lecture.accepts_upload_at(self.uploaded_at):
            self.status = self.Status.OUTSIDE_WINDOW
        elif QRCodeUpload.objects.filter(
            student=self.student,
            lecture=self.lecture,
            status=self.Status.ACCEPTED,
        ).exclude(pk=self.pk).exists():
            self.status = self.Status.DUPLICATE
        else:
            self.status = self.Status.ACCEPTED

        if self.status != self.Status.ACCEPTED:
            logger.warning("Rejected upload %s: %s", self.pk, self.status)
        self.save()
        return self.status

    def __str__(self):
        return f"{self.student} @ {self.uploaded_at:%Y-%m-%d %H:%M} ({self.status})"




# ---------------------------------------------------------------------------
# Helper / API functions
# ---------------------------------------------------------------------------

def attendance_rate(student, course):
    """
    Fraction of the course's meetings so far that `student` attended
    (has an ACCEPTED upload for), counting only meetings on or after the
    day they enrolled.  Returns None if no such meetings have happened.
    """
    enrollment = Enrollment.objects.get(student=student, course=course)
    held = course.lectures.filter(
        date__gte=timezone.localdate(enrollment.enrolled_at),
        date__lte=timezone.localdate(),
    )
    if not held.exists():
        return None
    attended = held.filter(
        uploads__student=student,
        uploads__status=QRCodeUpload.Status.ACCEPTED,
    ).distinct()
    return attended.count() / held.count()


def shared_photo_hashes():
    """
    Return image hashes uploaded by more than one distinct student -- a sign
    that a photo of the QR code was forwarded.  Look up the uploads with
    QRCodeUpload.objects.filter(image_hash__in=shared_photo_hashes()).
    """
    return (
        QRCodeUpload.objects.exclude(image_hash="")
        .values("image_hash")
        .annotate(n_students=models.Count("student", distinct=True))
        .filter(n_students__gt=1)
        .values_list("image_hash", flat=True)
    )



