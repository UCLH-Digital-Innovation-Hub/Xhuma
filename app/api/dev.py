import os

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import select

from ..audit.db_models import AuditEventRow
from ..audit.models import _subject_ref_from_nhs_number
from ..security import verify_api_key

router = APIRouter()


@router.get(
    "/_dev/audit",
    response_class=HTMLResponse,
    dependencies=[Depends(verify_api_key)],
)
async def dev_audit_form():
    return HTMLResponse("""
            <html>
              <head>
                <title>Dev Audit Viewer</title>
                <style>
                  body { font-family: sans-serif; margin: 2rem; }
                  input { padding: 0.5rem; width: 22rem; }
                  button { padding: 0.5rem 1rem; }
                  table { border-collapse: collapse; margin-top: 1rem; width: 100%; }
                  th, td { border: 1px solid #ddd; padding: 0.5rem; font-size: 0.9rem; }
                  th { background: #f5f5f5; text-align: left; }
                  .muted { color: #666; font-size: 0.9rem; }
                </style>
              </head>
              <body>
                <h2>Dev Audit Viewer</h2>
                <p class="muted">Queries by <code>subject_ref</code> derived from NHS number (raw NHS number is not stored).</p>

                <form method="post" action="/_dev/audit">
                  <label>NHS number:</label><br/>
                  <input name="nhs_number" placeholder="e.g. 9690937278" />
                  <button type="submit">Search</button>
                </form>
              </body>
            </html>
            """)


@router.post(
    "/_dev/audit",
    response_class=HTMLResponse,
    dependencies=[Depends(verify_api_key)],
)
async def dev_audit_query(request: Request, nhs_number: str = Form(...)):
    # --- safety: dev only ---
    secret = os.getenv("API_KEY")
    if not secret:
        return HTMLResponse(
            "<h3>Missing API_KEY</h3>",
            status_code=500,
        )

    try:
        subject_ref = _subject_ref_from_nhs_number(nhs_number, secret)
    except ValueError as e:
        return HTMLResponse(
            f"<h3>Invalid NHS number</h3><p>{e}</p>",
            status_code=400,
        )

    SessionLocal = getattr(request.app.state, "SessionLocal", None)
    if SessionLocal is None:
        return HTMLResponse(
            "<h3>Database session not configured</h3>",
            status_code=500,
        )

    stmt = (
        select(AuditEventRow)
        .where(AuditEventRow.subject_ref == subject_ref)
        .order_by(AuditEventRow.sequence.desc())
        .limit(500)
    )

    def esc(s: str) -> str:
        return (
            (s or "")
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;")
            .replace("'", "&#39;")
        )

    try:
        async with SessionLocal() as session:  # type: AsyncSession
            result = await session.execute(stmt)
            rows = result.scalars().all()
    except Exception as e:
        return HTMLResponse(
            f"<h3>Query failed</h3><pre>{esc(repr(e))}</pre>",
            status_code=500,
        )

    # --- render ---
    out: list[str] = []
    out.append("<html><head><title>Dev Audit Results</title></head><body style='font-family:sans-serif;margin:2rem;'>")
    out.append("<a href='/_dev/audit'>← back</a>")
    out.append("<h2>Audit results</h2>")
    out.append(f"<p class='muted'>subject_ref: <code>{esc(subject_ref)}</code></p>")
    out.append(f"<p>{len(rows)} row(s) returned (max 500).</p>")

    out.append("<table>")
    out.append(
        "<tr>"
        "<th>Time (UTC)</th><th>Seq</th><th>Action</th><th>Outcome</th><th>Error</th>"
        "<th>User</th><th>Role</th><th>Org</th>"
        "<th>Request ID</th><th>Trace ID</th>"
        "<th>Doc ID</th><th>Msg ID</th>"
        "</tr>"
    )

    for r in rows:
        out.append(
            "<tr>"
            f"<td>{esc(str(r.event_time))}</td>"
            f"<td>{esc(str(r.sequence))}</td>"
            f"<td>{esc(r.action)}</td>"
            f"<td>{esc(r.outcome)}</td>"
            f"<td>{esc(r.error_code or '')}</td>"
            f"<td>{esc(r.user_id or '')}</td>"
            f"<td>{esc(r.user_role_name or '')}</td>"
            f"<td>{esc(r.organisation or '')}</td>"
            f"<td>{esc(r.request_id or '')}</td>"
            f"<td>{esc(r.trace_id or '')}</td>"
            f"<td>{esc(r.document_id or '')}</td>"
            f"<td>{esc(r.message_id or '')}</td>"
            "</tr>"
        )

    out.append("</table></body></html>")
    return HTMLResponse("".join(out))
