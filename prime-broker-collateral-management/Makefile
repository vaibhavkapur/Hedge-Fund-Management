PYTHON ?= python3
PYTHONPATH := src

.PHONY: test demo reports verify-repro check-all clean

test:
	PYTHONPATH=$(PYTHONPATH) $(PYTHON) -m unittest discover -s tests -v

demo:
	PYTHONPATH=$(PYTHONPATH) $(PYTHON) scripts/run_demo.py

reports:
	PYTHONPATH=$(PYTHONPATH) $(PYTHON) scripts/generate_reports.py

verify-repro:
	PYTHONPATH=$(PYTHONPATH) $(PYTHON) scripts/verify_reproducibility.py

check-all: test reports verify-repro

clean:
	rm -rf .pytest_cache src/pb_collateral/__pycache__ tests/__pycache__ scripts/__pycache__ reports/*.db
