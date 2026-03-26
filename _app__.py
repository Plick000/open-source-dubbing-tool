import tempfile
from pathlib import Path

import streamlit as st

from _report_generator__ import generate_report

st.set_page_config(page_title="Jobs ZIP → Markdown Report", layout="wide")
st.title("Jobs ZIP → Markdown Report")
st.caption("Drag & drop JOBS.zip → generates _Report__YYYYMMDDHH__.md (filename is UTC hour).")

tz = st.selectbox("Display timezone inside report", ["UTC", "PKT"], index=0)
keep_extract = st.checkbox("Keep extracted ZIP (debug)", value=False)

uploaded = st.file_uploader("Upload JOBS.zip", type=["zip"])

if uploaded:
    with st.spinner("Generating report..."):
        tmp_zip = Path(tempfile.mkstemp(suffix=".zip")[1])
        tmp_zip.write_bytes(uploaded.getvalue())

        out_dir = Path(tempfile.mkdtemp(prefix="jobs_report_out_"))
        report_path = generate_report(tmp_zip, out_dir=out_dir, tz=tz, keep_extract=keep_extract)

        md = report_path.read_text(encoding="utf-8", errors="replace")

    st.success("Report generated.")
    st.download_button(
        label=f"Download {report_path.name}",
        data=md.encode("utf-8"),
        file_name=report_path.name,
        mime="text/markdown",
    )

    st.divider()
    st.subheader("Preview")
    st.markdown(md)