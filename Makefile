# Every target is a thin wrapper over `python -m subtitler.steps.<name>`.
# The steps are the interface; this file exists so the common ones are one word
# and so the order is written down somewhere.
#
#   make all V=IMG_2891     everything up to the subtitles, stopping at the gates
#   make review             open the review app, both gates live here
#   make check              the end-to-end test over the reference job

.DEFAULT_GOAL := help
.PHONY: help all audio transcribe diff corrections terms sentences translate \
        resegment qc render dub review guidelines titles langpack check test \
        clean new-job

PY  := uv run python -m subtitler.steps
V   ?=
PORT ?= 8731

# Steps that act on one video refuse to guess which one.
define need_video
	@if [ -z "$(V)" ]; then \
		echo "this target needs a video: make $@ V=IMG_2891"; \
		echo "(the basename of the file in media/, without its extension)"; \
		exit 1; \
	fi
endef

help:
	@echo "Per video (V=<basename in media/>):"
	@echo "  audio        extract speech-recognition-ready audio"
	@echo "  transcribe   run every configured engine"
	@echo "  diff         compare the engines, write the review document"
	@echo "  corrections  re-import the review document after editing it"
	@echo "  sentences    rebuild whole sentences from the corrected text"
	@echo "  translate    translate them, via the API or an agent prompt"
	@echo "  resegment    cut the translation into cues, write the SRTs"
	@echo "  qc           the gate before render, must pass"
	@echo "  render       soft-muxed and burned-in video"
	@echo "  dub          voiceover over the ducked original (DUB_ARGS=--spend)"
	@echo "  all          audio through qc, stopping wherever a human is needed"
	@echo ""
	@echo "Whole job:"
	@echo "  review       the review app on http://127.0.0.1:$(PORT)"
	@echo "  guidelines   write the translation brief for this language pair"
	@echo "  terms        collect glossary candidates (TERMS_ARGS=--propose)"
	@echo "  titles       name the videos from their transcripts"
	@echo "  langpack     generate a language pack (LANG=es)"
	@echo ""
	@echo "Development:"
	@echo "  test         the full suite"
	@echo "  check        the end-to-end test over the reference job"
	@echo "  new-job      strip this clone back to a blank job"

# ---------------------------------------------------------------- per video

audio:
	$(need_video)
	$(PY).audio $(V)

transcribe:
	$(need_video)
	$(PY).transcribe $(V)

diff:
	$(need_video)
	$(PY).diff $(V)

corrections:
	$(need_video)
	$(PY).corrections $(V)

sentences:
	$(need_video)
	$(PY).sentences $(V)

translate:
	$(need_video)
	$(PY).translate $(V)

resegment:
	$(need_video)
	$(PY).resegment $(V)

qc:
	$(need_video)
	$(PY).qc $(V)

render:
	$(need_video)
	$(PY).qc $(V)
	$(PY).render $(V)

# Deliberately not part of `all`: optional, and paid on some engines. Runs off
# the translated sentences, so it needs `translate` but not `render`.
dub:
	$(need_video)
	$(PY).dub $(V) $(DUB_ARGS)

# Stops at the first step that needs something it does not have: an unreviewed
# transcript, an unfilled glossary, an unanswered translation prompt. That is
# the intended behaviour, not a failure.
all:
	$(need_video)
	$(PY).audio $(V)
	$(PY).transcribe $(V)
	$(PY).diff $(V)
	$(PY).corrections $(V)
	$(PY).sentences $(V)
	$(PY).translate $(V)
	$(PY).resegment $(V)
	$(PY).qc $(V)

# ---------------------------------------------------------------- whole job

review:
	$(PY).review --port $(PORT)

guidelines:
	$(PY).guidelines

terms:
	$(PY).terms $(TERMS_ARGS)

titles:
	$(PY).titles

langpack:
	@if [ -z "$(LANG_CODE)" ]; then \
		echo "which language? make langpack LANG_CODE=es"; exit 1; fi
	$(PY).langpack_gen $(LANG_CODE)

# -------------------------------------------------------------- development

test:
	uv run pytest -q

check:
	uv run pytest tests/test_end_to_end.py -q

clean:
	rm -rf work/review/audio
	@echo "removed the audio cache; everything else under work/ is real output"

new-job:
	uv run python scripts/new_job.py
