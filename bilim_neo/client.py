import uuid
from typing import Dict, Any, List, Optional, Tuple
from datetime import datetime
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


class BilimClassClient:
    """
    Клиент для взаимодействия с API платформы BilimClass (bilimclass.kz).
    Поддерживает получение профиля, расписания, оценок (ФО, СОР, СОЧ), табеля и посещаемости.
    """
    BASE_URL = "https://api.bilimclass.kz"
    JOURNAL_URL = "https://journal-service.bilimclass.kz"

    def __init__(self, login: Optional[str] = None, password: Optional[str] = None):
        self.username = login
        self.password = password
        self.session = requests.Session()
        # BilimClass occasionally returns transient 5xx responses. Retry reads only;
        # never repeat a login POST whose outcome is unknown.
        self.session.mount("https://", HTTPAdapter(max_retries=Retry(
            total=2, backoff_factor=0.5, status_forcelist=[500, 502, 503, 504],
            allowed_methods=["GET"], raise_on_status=False)))
        self.device_uuid = str(uuid.uuid4())

        self.access_token: Optional[str] = None
        self.refresh_token: Optional[str] = None
        self.chat_token: Optional[str] = None
        self.user_info: Dict[str, Any] = {}
        self.school_id: Optional[int] = None
        self.user_id: Optional[int] = None
        self.group_id: Optional[int] = None
        self.group_uuids: List[str] = []
        now = datetime.now()
        self.current_edu_year: int = now.year if now.month >= 8 else now.year - 1

        self.session.headers.update({
            "User-Agent": (
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            ),
            "Accept": "application/json",
            "Content-Type": "application/json",
            "X-Localization": "ru",
            "X-Mathrix": self.device_uuid,
            "Origin": "https://www.bilimclass.kz",
            "Referer": "https://www.bilimclass.kz/"
        })

    def login(self, username: Optional[str] = None, password: Optional[str] = None) -> Dict[str, Any]:
        """
        Авторизация в BilimClass.
        Возвращает словарь с ответом сервера при успешном входе.
        """
        if username:
            self.username = username
        if password:
            self.password = password

        if not self.username or not self.password:
            raise ValueError("Требуется указать логин и пароль")

        url = f"{self.BASE_URL}/api/v2/os/login"
        payload = {
            "login": self.username.strip(),
            "password": self.password
        }

        resp = self.session.post(url, json=payload, timeout=15)
        if resp.status_code != 200:
            raise RuntimeError(f"Ошибка авторизации ({resp.status_code})")

        data = resp.json()
        if not data.get("success", False) and "access_token" not in data:
            raise RuntimeError("Не удалось авторизоваться")

        self.access_token = data.get("access_token")
        self.refresh_token = data.get("refresh_token")
        self.user_info = data.get("user_info", {})

        self.user_id = self.user_info.get("userId")
        self.chat_token = self.user_info.get("chatToken")
        self.school_id = self.user_info.get("school_id")

        group_info = self.user_info.get("group", {})
        self.group_id = group_info.get("id")

        student_info = self.user_info.get("studentInfo", {})
        self.group_uuids = student_info.get("studentGroupUuids", [])
        if not self.group_uuids and student_info.get("studentGroupUuid"):
            self.group_uuids = [student_info.get("studentGroupUuid")]

        # Определение текущего учебного года
        edu_years = self.user_info.get("school", {}).get("eduYears", [])
        for ey in edu_years:
            if ey.get("isCurrent"):
                self.current_edu_year = ey.get("eduYear", self.current_edu_year)
                break

        # Обновление заголовков авторизации сессии
        self.session.headers.update({
            "Authorization": f"Bearer {self.access_token}",
            "x-school-id": str(self.school_id or "")
        })

        return data

    def get_profile(self) -> Dict[str, Any]:
        """Информация о профиле ученика, классе и учебном заведении."""
        school = self.user_info.get("school", {})
        group = self.user_info.get("group", {})
        student_info = self.user_info.get("studentInfo", {})
        available_years = []
        for item in school.get("eduYears", []):
            try:
                available_years.append(int(item.get("eduYear")))
            except (TypeError, ValueError, AttributeError):
                pass

        return {
            "fio": f"{self.user_info.get('surname', '')} {self.user_info.get('firstname', '')} {self.user_info.get('lastname', '')}".strip(),
            "userId": self.user_id,
            "bornDate": self.user_info.get("bornDate"),
            "schoolName": school.get("name"),
            "region": school.get("region"),
            "schoolAddress": school.get("address"),
            "group": group.get("name"),
            "groupId": self.group_id,
            "iin": student_info.get("iin"),
            "currentEduYear": self.current_edu_year,
            "availableEduYears": sorted(set(available_years + [self.current_edu_year]), reverse=True),
        }

    def get_weeks(self, edu_year: Optional[int] = None) -> Tuple[List[Dict[str, Any]], str]:
        """
        Получить список учебных недель и дату текущей недели.
        """
        year = edu_year or self.current_edu_year
        url = f"{self.BASE_URL}/api/v4/os/clientoffice/diary/weeks"
        params = {
            "schoolId": self.school_id,
            "eduYear": year
        }
        resp = self.session.get(url, params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json().get("data", {})
        return data.get("weeks", []), data.get("week", "")

    def get_schedule(self, date: Optional[str] = None, edu_year: Optional[int] = None) -> Dict[str, Any]:
        """
        Получить расписание уроков на неделю.

        :param date: дата понедельника недели в формате ДД.ММ.ГГГГ (если не указано — текущая неделя)
        :param edu_year: учебный год
        """
        year = edu_year or self.current_edu_year
        if not date:
            _, current_week_date = self.get_weeks(year)
            date = current_week_date

        url = f"{self.BASE_URL}/api/v4/os/clientoffice/diary"
        params = {
            "schoolId": self.school_id,
            "eduYear": year,
            "date": date,
            "groupId": self.group_id
        }
        resp = self.session.get(url, params=params, timeout=15)
        resp.raise_for_status()
        return resp.json().get("data", {})

    def get_homework_files(self, homework_uuid: str) -> List[Dict[str, Any]]:
        """Current attachment metadata and short lived links for one homework item."""
        if not homework_uuid:
            return []
        url = f"{self.BASE_URL}/api/v4/os/clientoffice/homeworks/simple-homework/info"
        resp = self.session.get(url, params={
            "homeworkUuid": homework_uuid,
            "schoolId": self.school_id,
            "eduYear": self.current_edu_year,
        }, timeout=15)
        resp.raise_for_status()
        data = resp.json().get("data") or {}
        files = data.get("files") or []
        if not isinstance(files, list):
            raise RuntimeError("BilimClass returned invalid homework files")
        return files

    def get_periods(self, edu_year: Optional[int] = None) -> List[Dict[str, Any]]:
        """Получить список учебных периодов (четвертей)."""
        year = edu_year or self.current_edu_year
        url = f"{self.BASE_URL}/api/v4/os/clientoffice/diary/periods"
        params = {"schoolId": self.school_id, "eduYear": year}
        resp = self.session.get(url, params=params, timeout=15)
        resp.raise_for_status()
        return resp.json().get("data", {}).get("periods", [])

    def get_subjects_and_schedules(self, period: int = 1, period_type: str = "quarter", edu_year: Optional[int] = None) -> List[Dict[str, Any]]:
        """Получить список предметов и сопоставления расписаний за период."""
        year = edu_year or self.current_edu_year
        url = f"{self.BASE_URL}/api/v4/os/clientoffice/diary/subjects"
        params = {
            "schoolId": self.school_id,
            "eduYear": year,
            "periodType": period_type,
            "period": period
        }
        resp = self.session.get(url, params=params, timeout=15)
        resp.raise_for_status()
        return resp.json().get("data", [])

    def get_current_marks(self, period: int = 1, edu_year: Optional[int] = None) -> List[Dict[str, Any]]:
        """
        Получить все текущие оценки за указанную четверть:
        Формирующее оценивание (ФО), СОР, СОЧ, комментарии учителей и пропуски.
        """
        year = edu_year or self.current_edu_year
        periods = self.get_periods(year)
        target_period = next((p for p in periods if str(p.get("period")) == str(period)), None)

        if not target_period:
            raise RuntimeError("BilimClass не вернул даты учебного периода")
        d_start = datetime.strptime(target_period["periodStart"], "%d.%m.%Y")
        d_end = datetime.strptime(target_period["periodEnd"], "%d.%m.%Y")
        date_from_iso = d_start.strftime("%Y-%m-%dT00:00:00.000Z")
        date_to_iso = d_end.strftime("%Y-%m-%dT00:00:00.000Z")

        # Получаем сопоставление scheduleUuid -> предмет
        subjects_data = self.get_subjects_and_schedules(period=period, edu_year=year)
        sched_to_subject = {}
        for sub in subjects_data:
            s_name = sub.get("subjectName")
            s_id = sub.get("subjectId")
            for sch in sub.get("schedules", []):
                sch_uuid = sch.get("uuid")
                sched_to_subject[sch_uuid] = {
                    "subjectName": s_name,
                    "subjectId": s_id,
                    "date": sch.get("date"),
                    "timeStart": sch.get("timeStart"),
                    "type": sch.get("type")
                }

        # Запрашиваем журнал с оценками из микросервиса
        headers_journal = {
            "User-Agent": self.session.headers["User-Agent"],
            "Accept": "application/json",
            "Authorization": f"Bearer {self.chat_token}",
            "external": "1",
            "Origin": "https://www.bilimclass.kz",
            "Referer": "https://www.bilimclass.kz/"
        }

        all_scores = {}
        successful_groups = 0
        for guuid in self.group_uuids:
            try:
                r = self.session.get(
                    f"{self.JOURNAL_URL}/diary/quarter",
                    headers=headers_journal,
                    params={
                        "userId": self.user_id,
                        "studentGroupUuid": guuid,
                        "dateFrom": date_from_iso,
                        "dateTo": date_to_iso
                    },
                    timeout=15
                )
                if r.status_code == 200:
                    data = r.json().get("data", {})
                    if isinstance(data, dict):
                        successful_groups += 1
                        all_scores.update(data)
            except Exception:
                pass
        if self.group_uuids and successful_groups == 0:
            raise RuntimeError("Журнал оценок временно недоступен")

        marks_list = []
        for sch_uuid, score_info in all_scores.items():
            fscore = score_info.get("formattedScore") or {}
            sor = score_info.get("sor") or {}
            soch = score_info.get("soch") or {}
            po = score_info.get("po") or {}
            att = score_info.get("attendance")

            meta = sched_to_subject.get(sch_uuid, {})
            subject_name = meta.get("subjectName", "Неизвестный предмет")
            lesson_date = meta.get("date")

            has_mark = any(
                item.get("mark") is not None
                for item in [fscore, sor, soch, po]
            )

            if has_mark or (att and att != "was_in_class"):
                entry = {
                    "scheduleUuid": sch_uuid,
                    "subject": subject_name,
                    "date": lesson_date,
                    "attendance": att,
                    "regular_mark": fscore.get("mark"),
                    "regular_max": fscore.get("markMax"),
                    "regular_comment": fscore.get("comment"),
                    "sor_mark": sor.get("mark"),
                    "sor_max": sor.get("markMax"),
                    "sor_comment": sor.get("comment"),
                    "soch_mark": soch.get("mark"),
                    "soch_max": soch.get("markMax"),
                    "soch_comment": soch.get("comment"),
                    "po_mark": po.get("mark"),
                    "po_max": po.get("markMax"),
                    "po_comment": po.get("comment"),
                }
                marks_list.append(entry)

        def parse_date(x):
            d = x.get("date")
            if d:
                try:
                    return datetime.strptime(d, "%d.%m.%Y")
                except Exception:
                    pass
            return datetime.min

        marks_list.sort(key=parse_date)
        return marks_list

    def get_year_grades(self, edu_year: Optional[int] = None) -> List[Dict[str, Any]]:
        """
        Получить итоговый табель успеваемости (четверти 1-4, годовая, итоговая оценки).
        """
        year = edu_year or self.current_edu_year
        url = f"{self.BASE_URL}/api/v4/os/clientoffice/diary/year"
        params = {"schoolId": self.school_id, "eduYear": year}
        resp = self.session.get(url, params=params, timeout=15)
        resp.raise_for_status()
        rows = resp.json().get("data", {}).get("rows", [])

        result = []
        for r in rows:
            name = r.get("subjectName")
            att = r.get("attestations", {})
            ys = r.get("yearScores", {})
            misses = r.get("attendances", {}).get("totalMissCount", 0)
            result.append({
                "subject": name,
                "q1": att.get("quarter1"),
                "q2": att.get("quarter2"),
                "q3": att.get("quarter3"),
                "q4": att.get("quarter4"),
                "yearScore": ys.get("yearScore"),
                "finalScore": ys.get("finalScore"),
                "totalMisses": misses
            })
        return result
