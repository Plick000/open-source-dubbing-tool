# Dubbing Automation Pipeline (V1) — README

This repository contains the **ViralVerse Dubbing Automation Pipeline** for Adobe Premiere Pro + After Effects + Adobe Media Encoder + ExtendScript + Python tooling.  
It automates the full workflow from **cut initialization**, **Actual generation with timestamps**, **final sequence building**, **interview dubbing**, **disclaimer injection**, **titles/lower-thirds translation & batch rendering**, **SFX/BGM retiming**, and **transitions automation**, ending with **automatic Premiere re-import and AME export**.

This README documents the pipeline exactly in the order it is intended to run.

---

## Table of Contents
- [Overview](#overview)
- [Pipeline Summary](#pipeline-summary)
- [Process 1 — Cut File](#process-1--cut-file)
- [Process 2 — Voice Generation with Timestamps](#process-2--voice-generation-with-timestamps)
- [Process 3 — Final Sequence](#process-3--final-sequence)
- [Process 4 — ElevenLabs Interviews Generations](#process-4--elevenlabs-interviews-generations)
- [Process 5 — Dubbing + Extra’s](#process-5--dubbing--extras)
- [Process 6 — Proper Interviews](#process-6--proper-interviews)
- [Process 7 — Titles & LowerThirds Fields](#process-7--titles--lowerthirds-fields)
- [Process 8 — Titles Automation](#process-8--titles-automation)
- [Process 9 — LowerThirds Calculations](#process-9--lowerthirds-calculations)
- [Process 10 — LowerThirds Batch Rendering](#process-10--lowerthirds-batch-rendering)
- [Process 11 — LowerThirds + BGM + SFX’s Automation](#process-11--lowerthirds--bgm--sfxs-automation)
- [Process 12 — Transitions Automation](#process-12--transitions-automation)
- [Files Included in This Repo](#files-included-in-this-repo)
- [Notes / Constraints](#notes--constraints)

---

## Overview

### Core idea
1. Export and analyze an **English Premiere sequence XML**.
2. Generate multiple stage JSONs (`A1__...` → `A24__...`) that represent detection + retiming decisions.
3. Generate staged timeline XML outputs (`V1__...xml` → `V9__...xml`).
4. Re-import into Premiere, clean tracks, shift/merge, adjust transitions, and export via AME.

---

## Pipeline Summary

**Python** does:
- detection, timestamps, retiming, timeline generation (FCPXML/XML editors), data merges, validations, copying artifacts.

**JSX (ExtendScript)** does:
- extracting track items, exporting/importing XML, track cleanup, shifting/merging sequences, transition adjustment, sending to AME.

**Batch rendering** (After Effects + Media Encoder) does:
- rendering Titles and LowerThirds as **mp4 clips** to avoid AE-graphics unlinking issues.

---

## Process 1 — Cut File

### Step 1 — Run Premiere pre-export automation
- **Python:** `__run_premiere_before_xml__.py`  
Purpose: Extract required sequence data and prepare export automation.

### Step 2 — Extract V5, V6 clips into duplicated sequence
- **JSX:** `_ExtractV5V6A4ItemsV1.jsx`  
Purpose: Extract V5/V6 clips and save into a duplicated sequence named:  
**“Transitions Titles - V3-4-5-6 A4”**

### Step 3 — Extract Titles & LowerThirds into JSON
- **JSX:** `_ExtractTitlesAndLowerThirds.jsx`  
Output: `__titles_and_lowerthirds__.json`

### Step 4 — Export XML of sequence
- **JSX:** `_ExportXMLSequence.jsx`  
Important: Export XML into the **Dubbing Automation Tool required location**.

### Step 4.5 — Validate XML exported
- **Python:** `__xml_validation__.py`  
Rule: If XML is not exported/invalid, **do not start next steps**.

### Step 5 — Initialize cuts
- **Python:** `__cuts_detections__.py`  
Output: `A1__dubbing__.json`

### Step 6 — Implement cuts + label split cuts
- **Python:** `__v1_cuts_implementor__.py`  
Output: `V1__dubbing__.xml`

### Step 7 — Initialize cuts again on V1 track (timeline detection)
- **Python:** `__timeline_detections__.py`  
Output: `A2__dubbing__.json`

---

## Process 2 — Voice Generation with Timestamps

### Step 8 — Generate voice + alignments JSON
- **Python:** `__elevenlabs_audio_alignments_generator__.py`  
Attachments: Script (paste form)  
Output: Alignments JSON (used downstream)

### Step 9 — Generate chunk timestamps for dubbed language (e.g., Polish)
- **Python:** `__chunks_timestamps_cal__.py`  
Attachments: `alignments.json` + `chunks file` (paste form)  
Output: `A3__dubbing__.json`

---

## Process 3 — Final Sequence

### Step 10 — Build final dubbing sequence JSON
- **Python:** `__timeline_manager__.py`  
Attachments:
- `A2__dubbing__.json` (from Step 7)
- `A3__dubbing__.json` (from Step 9)

Output: `A4__dubbing__.json`

---

## Process 4 — ElevenLabs Interviews Generations

### Step 11 — Extract full video timestamps (V1, A1, A2)
- **Python:** `__timestamps_V1_A1_A2__.py`  
Output: `A5__timestamps__.json`

### Step 12 — Extract interview clip timestamps
- **Python:** `__extract_interview_timestamps__.py`  
Output: `A6__interviews__.json`

### Step 13 — Extract interview audio clips (ffmpeg)
- **Python:** `__ffmpeg_clips_extraction__.py`  
Attachments: English Rendered Video (must contain interview audio)  
Output: Extracted interview audio clips (`.mp3`)

### Step 14 — Dub the interview clips
- **Python:** `__interviews_dubbing__.py`  
Output: Dubbed interview clips

### Step 15 — Generate interview dubbed character segments (25–40 chars)
- **Python:** `__words_segments__.py`  
Output: `A7__segments__.json`

---

## Process 5 — Dubbing + Extra’s

### Step 16 — Dub the V1 clips into FCPXML editor stage
- **Python:** `__timeline_fcpxml_V1__.py`  
Output: `V2__dubbing__.xml`

### Step 17 — Get disclaimer by brand + language
- **Python:** `__states_disclaimers__.py`  
Example: Polish Video 7 → Brand 7  
Output: `A8__disclaimer__.json`

### Step 18 — Add extras (Disclaimer + Dubbed Audio)
- **Python:** `__timeline_fcpxml_V2__.py`  
Output: `V3__dubbing__.xml`

---

## Process 6 — Proper Interviews

### Step 19 — Add interviews into the timeline
- **Python:** `__timeline_fcpxml_V3__.py`  
Output: `V4__dubbing__.xml`

### Step 20 — Calculate interview line timestamps to match audio
- **Python:** `__timestamps_interviews__.py`  
Output: `A9__absolute_interviews__.json`

### Step 21 — Encode + update interview text
- **Python:** `__timeline_fcpxml_V4__.py`  
Output: `V5__dubbing__.xml`

---

## Process 7 — Titles & LowerThirds Fields

### Step 22 — Similarity check for titles/lower-thirds chunk mapping
- **Python:** `__segments_similarity__.py`  
Output: `A10__matched_segments__.json`

### Step 23 — Translate titles/lower-thirds segments via Gemini CLI
- **Python:** `__gemini_translation_cli__.py`  
Output: `A11__translated_segments__.json`

### Step 24 — Validate/match translated segments (chunk mapping improvements)
- **Python:** `__gemini_segments_validations__.py`  
Output: `A12__final_segments__.json`

### Step 25 — Merge title objects with dubbed-language text + image names
- **Python:** `__titles_objects_merged__.py`  
Output: `A13__titles_segments__.json`

### Step 26 — Titles “states” selection (like disclaimers but for titles)
- **Python:** `__states_titles__.py`  
Output: `A14__title__.json`

---

## Process 8 — Titles Automation

### Sub Process — Batch Running for Titles (Background)

Goal: Render each title as **mp4** (full video format) so final XML uses video clips instead of AE graphics (prevents unlinking).

**Batch File 1**
- **CLI:** `_TitleRender__.bat`  
Purpose: Run After Effects in background, execute script, save project, close AE.

**Batch File 2**
- **JSX:** `_TitleTextAndImageReplace.jsx`  
Purpose: Read current text/image → replace with new → send to Media Encoder with **Titles preset**.

### Step 27 — Prepare and orchestrate title rendering jobs
- **Python:** `__batch_titles_rendering__.py`  
Output: Rendered title video clips inside Titles folder per language

### Step 27.5 — Validate titles exported
- **Python:** `__titles_validation__.py`  
Rule: If titles not exported, **do not start next steps**.

### Step 28 — Extract English timestamps of titles & lower-thirds (tracks V3/V4)
- V3 = Titles  
- V4 = LowerThirds  
- **Python:** `__extract_V3_V4_timestamps__.py`  
Output: `A15__V3_V4_clips_timestamps__.json`

### Step 29 — Calculate dubbed-language timestamps for titles
- **Python:** `__timestamps_titles_cal__.py`  
Outputs:
- `A16__final_titles_segments__.json`
- `__final_titles_segments_report__.json`

### Step 30 — Place titles using mp4 title clips + remove previous graphic titles
- **Python:** `__timeline_fcpxml_V5__.py`  
Output: `V6__dubbing__.xml`

---

## Process 9 — LowerThirds Calculations

### Step 31 — Extract lower-thirds from final titles & lower-thirds JSON
- **Python:** `__extract_lowerthirds__.py`  
Output: `A17__extracted_lowerthirds__.json`

### Step 32 — Compute final lower-thirds timestamps
- **Python:** `__timestamps_lowerthirds_cal__.py`  
Output: `A18__computed_lowerthirds__.json`

---

## Process 10 — LowerThirds Batch Rendering

### Step 33 — LowerThirds “states” selection (like disclaimers but for lower-thirds)
- **Python:** `__states_lowerthirds__.py`  
Output: `A19__lowerthird__.json`

### Sub Process — Batch Running for LowerThirds (Background)

**Batch File 1**
- **CLI:** `_LowerThirdRender__.bat`  
Purpose: Run AE in background, render, save project, close AE.

**Batch File 2**
- **JSX:** `_ReplaceTextAndRender.jsx`  
Purpose: Read current text → replace → render.

### Step 34 — Prepare and orchestrate lower-thirds rendering jobs
- **Python:** `__batch_lowerthirds_rendering__.py`  
Output: Rendered lower-third clips inside LowerThirds folder per language

### Step 34.5 — Validate lower-thirds exported
- **Python:** `__lowerthirds_validation__.py`  
Rule: If not exported, **do not start next steps**.

---

## Process 11 — LowerThirds + BGM + SFX’s Automation

### Step 35 — Place lower-thirds with new absolute values
- **Python:** `__timeline_fcpxml_V6__.py`  
Output: `V7__dubbing__.xml`

### Step 36 — Extract timestamps for SFX on A6/A7
- **Python:** `__extract_A6_A7_timestamps__.py`  
Output: `A23__A6_A7_timestamps__.json`

### Step 37 — Compute final SFX timestamps for new locations
- **Python:** `__timestamps_SFX_cal__.py`  
Output: `A24__final_SFX_timestamps__.json`

### Step 38 — Move SFX according to new dubbed timestamps
- **Python:** `__timeline_fcpxml_V7__.py`  
Output: `V8__dubbing__.xml`

### Step 38 (BGM) — Edit background music with new timestamps
- **Python:** `__timeline_fcpxml_V8__.py`  
Outputs:
- `V9__dubbing__.xml`
- `A20__BGM__.json`

---

## Process 12 — Transitions Automation

### Step 39 — Extract transitions timestamps of English version
- **Python:** `__timeline_transitions_detection__.py`  
Output: `A21__transitions_V5_V6_timestamps__.json`

### Step 40 — Compute final timestamps for transitions adjustments
- **Python:** `__transitions_timestamps_cal__.py`  
Output: `A22__transitions_segments__.json`

### Step 41 — Copy final XML to required Windows location
- **Python:** `__copy_xml_to_windows__.py`

### Step 42 — Copy final transitions segments to required Windows location
- **Python:** `__copy_transitions_segments_to_windows__.py`

### Step 43 — Run Premiere post-XML automation
- **Python:** `__run_premiere_after_xml__.py`

### Step 44 — Re-import new XML into Premiere
- **JSX:** `_ImportXML.jsx`

### Step 45 — Remove V5, V6, A5 clips from tracks
- **JSX:** `_RemoveV5V6A5TrackItemsV1.jsx`

### Step 46 — Merge/shift sequence together (final alignment)
- **JSX:** `_ShiftOrMergeSequence.jsx`

### Step 47 — Apply transitions adjustments
- **JSX:** `_AdjustTransitions.jsx`

### Step 48 — Send final sequence to AME
- **JSX:** `_SendToAME.jsx`

---

## Files Included in This Repo

### Python (V1)
- `__copy_project_file__.py`
- `__run_premiere_before_xml__.py`
- `__xml_validation__.py`
- `__cuts_detections__.py`
- `__v1_cuts_implementor__.py`
- `__timeline_detections__.py`
- `__chunks_timestamps_cal__.py`
- `__timeline_manager__.py`
- `__timestamps_V1_A1_A2__.py`
- `__extract_interview_timestamps__.py`
- `__ffmpeg_clips_extraction__.py`
- `__interviews_dubbing__.py`
- `__words_segments__.py`
- `__timeline_fcpxml_V1__.py`
- `__states_disclaimers__.py`
- `__timeline_fcpxml_V2__.py`
- `__timeline_fcpxml_V3__.py`
- `__timestamps_interviews__.py`
- `__timeline_fcpxml_V4__.py`
- `__segments_similarity__.py`
- `__gemini_translation_cli__.py`
- `__gemini_segments_validations__.py`
- `__titles_objects_merged__.py`
- `__extract_V3_V4_timestamps__.py`
- `__timestamps_titles_cal__.py`
- `__merge_titles_segments__.py`
- `__extract_lowerthirds__.py`
- `__timestamps_lowerthirds_cal__.py`
- `__extract_A6_A7_timestamps__.py`
- `__timestamps_SFX_cal__.py`
- `__timeline_fcpxml_V7__.py`
- `__timeline_fcpxml_V8__.py`
- `__copy_xml_to_windows__.py`
- `__timeline_transitions_detection__.py`
- `__transitions_timestamps_cal__.py`
- `__copy_transitions_segments_to_windows__.py`
- `__run_premiere_after_xml__.py`

> Additional scripts referenced by the pipeline but not listed above may exist in other repos/branches (e.g., `__states_titles__.py`, `__states_lowerthirds__.py`, title/lowerthird validation + batch rendering tools, etc.).

### JSX (V1)
- `_SequenceTimebase24FPS.jsx`
- `_ExtractV5V6A4ItemsV1.jsx`
- `_ExtractTitlesAndLowerThirds.jsx`
- `_ExportXMLSequence.jsx`
- `_ImportXML.jsx`
- `_RemoveV5V6A5TrackItemsV1.jsx`
- `_ShiftOrMergeSequence.jsx`
- `_AdjustTransitions.jsx`
- `_ChangeTitlesTextAndMove.jsx`
- `_ChangeTitlesTextTwoFieldsAndMove.jsx`
- `_ChangeTitlesTextDetectionFieldsAndMove.jsx`
- `_ChangeLowerThirdsTextAndMove.jsx`
- `_SendToAME.jsx`

---

## Notes / Constraints

- Titles & LowerThirds alignment is handled through **batch rendering to mp4** to prevent unlinking and XML misalignment issues.
- Validations exist at key gates:
  - XML export validation (`__xml_validation__.py`)
  - Titles export validation (`__titles_validation__.py`)
  - LowerThirds export validation (`__lowerthirds_validation__.py`)
- Ensure export/import paths match your Dubbing Automation Tool folders and Windows/Premiere access paths.

---
