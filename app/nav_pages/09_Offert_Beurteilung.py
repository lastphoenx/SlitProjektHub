# app/pages/09_Offert_Beurteilung.py
"""Phase C — Offertbeurteilung: Matrix, Kriterien, Bieter, Export."""
from __future__ import annotations

import sys
from io import BytesIO
from pathlib import Path

import pandas as pd
import streamlit as st

try:
    import openpyxl  # noqa: F401
    HAS_OPENPYXL = True
except ImportError:
    HAS_OPENPYXL = False

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.auth_gate import require_auth
from src.m03_db import init_db
from src.m06_ui import render_global_llm_settings
from src.m07_projects import list_projects_df
from src.m09_docs import get_project_documents
from src.m14_auth import (
    can_evaluate,
    can_view_evaluator_details,
    get_user_id,
    is_super_user,
    session_username,
)
from src.m15_evaluation import (
    ANGEbot_CLASSIFICATION,
    CRITERION_KINDS,
    create_bidder,
    create_criterion,
    compute_rankings,
    get_bidder_document_ids,
    get_score,
    link_document_to_bidder,
    list_bidders,
    list_criteria,
    list_scores_for_project,
    soft_delete_bidder,
    soft_delete_criterion,
    suggest_score_with_rag,
    unlink_document_from_bidder,
    upsert_score,
)

require_auth()
init_db()

st.title("Offertbeurteilung (Phase C)")

token = st.session_state.get("_auth_token", "")
username = session_username(token) or ""
user_id = get_user_id(username)
may_evaluate = can_evaluate(username)
may_see_evaluators = can_view_evaluator_details(username)
super_user = is_super_user(username)

projects_df = list_projects_df()
if projects_df.empty:
    st.warning("Keine Projekte vorhanden.")
    st.stop()

project_options = {
    row["title"]: row["key"] for _, row in projects_df.iterrows()
}
project_titles = list(project_options.keys())
default_idx = 0
selected_title = st.selectbox("Projekt", project_titles, index=default_idx)
project_key = project_options[selected_title]

bidders = list_bidders(project_key)
criteria = list_criteria(project_key)
scores = list_scores_for_project(project_key)
score_map = {(s.bidder_id, s.criterion_id): s for s in scores}

if not may_evaluate:
    st.info(
        "Du kannst Rangfolge und aggregierte Matrix einsehen. "
        "Bewertungen erfassen nur Super-User, Projektleiter intern und Product Owner."
    )

tab_matrix, tab_bidders, tab_criteria, tab_export = st.tabs(
    ["Matrix & Rangfolge", "Bieter & Angebote", "Kriterien", "Export"]
)

with tab_matrix:
    st.subheader("Bewertungsmatrix")
    if not bidders or not criteria:
        st.caption("Zuerst Bieter und Kriterien anlegen.")
    else:
        matrix_rows = []
        for crit in criteria:
            row = {"Kriterium": crit.name, "Art": crit.kind}
            if crit.kind == "zuschlag" and crit.weight_pct:
                row["Gewicht %"] = crit.weight_pct
            for bidder in bidders:
                sc = score_map.get((bidder.id, crit.id))
                cell = f"{sc.value:.1f}" if sc else "—"
                row[bidder.name] = cell
            matrix_rows.append(row)
        st.dataframe(pd.DataFrame(matrix_rows), use_container_width=True, hide_index=True)

        rankings = compute_rankings(project_key)
        st.subheader("Rangfolge (gewichtet, nach Eignung)")
        rank_df = pd.DataFrame(
            [
                {
                    "Rang": r["rank"] if r["rank"] else "K.O./offen",
                    "Bieter": r["bidder_name"],
                    "Gesamt %": r["total_score"],
                    "Eignung OK": not r["ko"],
                }
                for r in rankings
            ]
        )
        st.dataframe(rank_df, use_container_width=True, hide_index=True)

    if may_evaluate and bidders and criteria:
        st.divider()
        st.subheader("Bewertung erfassen")
        render_global_llm_settings()
        llm_provider = st.session_state.get("global_llm_provider", "openai")
        llm_model = st.session_state.get("global_llm_model", "gpt-4o-mini")

        bidder_labels = {b.name: b for b in bidders}
        crit_labels = {f"{c.kind}: {c.name}": c for c in criteria}
        pick_bidder = st.selectbox("Bieter", list(bidder_labels.keys()))
        pick_crit = st.selectbox("Kriterium", list(crit_labels.keys()))
        bidder = bidder_labels[pick_bidder]
        criterion = crit_labels[pick_crit]
        existing = get_score(bidder.id, criterion.id)

        if existing and may_see_evaluators:
            st.caption(f"Letzte Bewertung: {existing.value} (User-ID {existing.evaluator_user_id})")

        if st.button("KI-Vorschlag (RAG)", key="eval_suggest"):
            with st.spinner("RAG + LLM …"):
                suggestion = suggest_score_with_rag(
                    project_key,
                    bidder.id,
                    criterion,
                    provider=llm_provider,
                    model=llm_model,
                )
            st.session_state["eval_suggestion"] = suggestion
            if suggestion.get("value") is None:
                st.warning("Kein LLM-Vorschlag — bitte manuell bewerten.")

        suggestion = st.session_state.get("eval_suggestion", {})
        default_val = suggestion.get("value")
        if existing and default_val is None:
            default_val = existing.value
        default_val = float(default_val or 0)

        with st.form("score_form"):
            value = st.number_input(
                f"Wert (0–{criterion.scale_max})",
                min_value=0.0,
                max_value=float(criterion.scale_max),
                value=default_val,
                step=0.5,
            )
            justification = st.text_area(
                "Begründung",
                value=suggestion.get("justification") or (existing.justification if existing else ""),
            )
            source_ref = st.text_area(
                "Zitat / Quellenreferenz",
                value=suggestion.get("source_chunk_ref") or (existing.source_chunk_ref if existing else ""),
            )
            submitted = st.form_submit_button("Speichern")

        if submitted and user_id:
            try:
                upsert_score(
                    bidder.id,
                    criterion.id,
                    user_id,
                    value,
                    justification=justification or None,
                    source_chunk_ref=source_ref or None,
                    allow_override=super_user,
                )
                st.success("Bewertung gespeichert.")
                st.session_state.pop("eval_suggestion", None)
                st.rerun()
            except (ValueError, PermissionError) as exc:
                st.error(str(exc))

with tab_bidders:
    st.subheader("Bieter")
    if may_evaluate:
        with st.form("new_bidder"):
            new_name = st.text_input("Neuer Bieter")
            add_b = st.form_submit_button("Anlegen")
        if add_b and new_name.strip():
            create_bidder(project_key, new_name.strip())
            st.rerun()

    offer_docs = [
        d for d in get_project_documents(project_key)
        if d.classification == ANGEbot_CLASSIFICATION
    ]
    if not offer_docs:
        st.caption(
            f"Keine Dokumente mit Klassifikation «{ANGEbot_CLASSIFICATION}» im Projekt. "
            "In Stammdaten hochladen und dem Projekt zuordnen."
        )

    for bidder in bidders:
        with st.expander(bidder.name, expanded=False):
            linked_ids = set(get_bidder_document_ids(bidder.id))
            if offer_docs:
                for doc in offer_docs:
                    checked = doc.id in linked_ids
                    new_checked = st.checkbox(
                        doc.filename,
                        value=checked,
                        key=f"bidder_doc_{bidder.id}_{doc.id}",
                    )
                    if new_checked != checked:
                        if new_checked:
                            link_document_to_bidder(bidder.id, doc.id)
                        else:
                            unlink_document_from_bidder(bidder.id, doc.id)
                        st.rerun()
            if may_evaluate and st.button("Bieter entfernen", key=f"del_bidder_{bidder.id}"):
                soft_delete_bidder(bidder.id)
                st.rerun()

with tab_criteria:
    st.subheader("Kriterien")
    if may_evaluate:
        with st.form("new_criterion"):
            c_name = st.text_input("Kriterium")
            c_kind = st.selectbox("Art", CRITERION_KINDS)
            c_weight = st.number_input("Gewicht % (nur Zuschlag)", min_value=0.0, value=10.0)
            c_scale = st.number_input("Skala max", min_value=1, value=10)
            add_c = st.form_submit_button("Anlegen")
        if add_c and c_name.strip():
            create_criterion(
                project_key,
                c_kind,
                c_name.strip(),
                weight_pct=c_weight if c_kind == "zuschlag" else 0.0,
                scale_max=int(c_scale),
            )
            st.rerun()

    crit_df = pd.DataFrame(
        [
            {
                "Art": c.kind,
                "Name": c.name,
                "Gewicht %": c.weight_pct if c.kind == "zuschlag" else "",
                "Skala": c.scale_max,
            }
            for c in criteria
        ]
    )
    if not crit_df.empty:
        st.dataframe(crit_df, use_container_width=True, hide_index=True)

    if may_evaluate:
        for crit in criteria:
            if st.button(f"Löschen: {crit.name}", key=f"del_crit_{crit.id}"):
                soft_delete_criterion(crit.id)
                st.rerun()

with tab_export:
    st.subheader("Bewertungsprotokoll")
    if not bidders or not criteria:
        st.caption("Noch keine Daten.")
    else:
        export_rows = []
        rankings = compute_rankings(project_key)
        rank_by_bidder = {r["bidder_id"]: r for r in rankings}
        for crit in criteria:
            for bidder in bidders:
                sc = score_map.get((bidder.id, crit.id))
                rank_row = rank_by_bidder.get(bidder.id, {})
                export_rows.append(
                    {
                        "Projekt": selected_title,
                        "Bieter": bidder.name,
                        "Kriterium": crit.name,
                        "Art": crit.kind,
                        "Gewicht %": crit.weight_pct if crit.kind == "zuschlag" else "",
                        "Wert": sc.value if sc else "",
                        "Skala max": crit.scale_max,
                        "Begründung": sc.justification if sc else "",
                        "Quelle": sc.source_chunk_ref if sc else "",
                        "Bewerter User-ID": sc.evaluator_user_id if sc and may_see_evaluators else "",
                        "Rang": rank_row.get("rank"),
                        "Gesamt %": rank_row.get("total_score"),
                    }
                )
        export_df = pd.DataFrame(export_rows)
        st.dataframe(export_df, use_container_width=True, hide_index=True)

        csv = export_df.to_csv(index=False).encode("utf-8-sig")
        st.download_button("Download CSV", csv, "bewertungsprotokoll.csv", "text/csv")

        if HAS_OPENPYXL:
            buf = BytesIO()
            with pd.ExcelWriter(buf, engine="openpyxl") as writer:
                export_df.to_excel(writer, index=False, sheet_name="Bewertungen")
                rank_export = pd.DataFrame(
                    [
                        {
                            "Rang": r["rank"],
                            "Bieter": r["bidder_name"],
                            "Gesamt %": r["total_score"],
                            "KO": r["ko"],
                        }
                        for r in rankings
                    ]
                )
                rank_export.to_excel(writer, index=False, sheet_name="Rangfolge")
            st.download_button(
                "Download Excel",
                buf.getvalue(),
                "bewertungsprotokoll.xlsx",
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        else:
            st.caption("Excel: pip install openpyxl")
