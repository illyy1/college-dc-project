from models import MaintenanceRequest, Technician

def validate_and_assign_request(request: MaintenanceRequest, tech_id: str, technicians_dict: dict[str, Technician]):
    if tech_id not in technicians_dict:
        raise ValueError(f"שגיאת אימות נתונים: טכנאי שמזהה '{tech_id}' אינו קיים במערכת!")

    request.assign_to(tech_id)
    technicians_dict[tech_id].assigned_requests.append(request)
    print(f"[SUCCESS] בקשה {request.request_id} שויכה בהצלחה לטכנאי {technicians_dict[tech_id].name}")


def generate_technician_workload_report(technicians_dict: dict[str, Technician]):
    print("\n" + "=" * 40)
    print("      דוח עסקי: עומס עבודה לפי טכנאי")
    print("=" * 40)

    for tech_id, tech in technicians_dict.items():
        open_tasks = [r for r in tech.assigned_requests if r.status != "RESOLVED"]
        print(f"• {tech.name} ({tech_id}) - התמחות: {tech.specialty}")
        print(f"  משימות פעילות לטיפול: {len(open_tasks)}")
        for task in open_tasks:
            print(f"    - {task}")
    print("=" * 40 + "\n")