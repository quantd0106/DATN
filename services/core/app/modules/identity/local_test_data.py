"""Offline synthetic identities for the dedicated dev_lumi_local database.

Run as a module with a JSON object on stdin containing admin_password,
student_password and teacher_password. Never logs credentials or tokens.
Uses normal invitation/activation flows inside one transaction.
"""

import json
import sys
from uuid import uuid4

from pydantic import SecretStr
from sqlalchemy import select, text

from app.common.db import SessionLocal
from app.common.security import verify_password
from app.model_registry import load_metadata
from app.modules.identity.models import User
from app.modules.identity.schemas import (
    ActivateStudentRequest,
    AdminCreateStudentRequest,
    AdminCreateUserRequest,
    ResetPasswordRequest,
)
from app.modules.identity.service import IdentityService


def main():
    config = json.load(sys.stdin)
    load_metadata()
    with SessionLocal() as session, session.begin():
        if session.scalar(text("SELECT current_database()")) != "dev_lumi_local":
            raise ValueError("Requires the dedicated dev_lumi_local database")
        session.execute(text("SELECT pg_advisory_xact_lock(726651115)"))
        admin = session.scalar(select(User).where(User.email == "admin@seed.example.com"))
        if admin is None or not verify_password(config["admin_password"], admin.password_hash):
            raise ValueError("Seed administrator authentication failed")
        admin.display_name = admin.display_name or "Quản trị viên thử nghiệm"
        for email, name in (
            ("primary@seed.example.com", "Giáo viên chính thử nghiệm"),
            ("assisting@seed.example.com", "Giáo viên phối hợp thử nghiệm"),
        ):
            baseline = session.scalar(select(User).where(User.email == email))
            if baseline is not None and baseline.school_id == admin.school_id:
                baseline.display_name = baseline.display_name or name
        service = IdentityService(session)
        students, teachers = [], []
        for grade in range(6, 13):
            for index in range(1, 21):
                username = f"local.hs{grade}.{index:02d}"
                user = session.scalar(select(User).where(User.username == username))
                if user is None:
                    code = service.admin_create_student(
                        AdminCreateStudentRequest(
                            username=username, display_name=f"Học sinh thử {grade}A - {index:02d}"
                        ),
                        actor_id=admin.id,
                        correlation_id=uuid4(),
                    )
                    service.activate_student(
                        ActivateStudentRequest(
                            username=username,
                            activation_code=code.code,
                            new_password=SecretStr(config["student_password"]),
                        ),
                        correlation_id=uuid4(),
                        commit=False,
                    )
                    user_id = code.student.id
                else:
                    if user.school_id != admin.school_id:
                        raise ValueError("Existing synthetic identity scope mismatch")
                    user_id = user.id
                students.append({"id": str(user_id), "username": username, "grade": grade})
        for index in range(1, 9):
            email = f"local.teacher{index:02d}@example.com"
            user = session.scalar(select(User).where(User.email == email))
            if user is None:
                user, invitation = service.admin_create_user(
                    AdminCreateUserRequest(
                        email=email,
                        roles=["LECTURER"],
                        display_name=f"Giáo viên thử nghiệm {index:02d}",
                    ),
                    actor_id=admin.id,
                    correlation_id=uuid4(),
                )
                service.reset_password(
                    ResetPasswordRequest(
                        token=invitation.token, new_password=SecretStr(config["teacher_password"])
                    ),
                    commit=False,
                )
            elif user.school_id != admin.school_id:
                raise ValueError("Existing synthetic identity scope mismatch")
            else:
                user.display_name = user.display_name or f"Giáo viên thử nghiệm {index:02d}"
            teachers.append({"id": str(user.id), "email": email})
    print(json.dumps({"students": students, "teachers": teachers}))


if __name__ == "__main__":
    main()
