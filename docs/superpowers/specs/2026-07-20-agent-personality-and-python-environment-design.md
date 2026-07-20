# Agent Personality and Python Environment Design

## Objective

Establish a durable operating contract for the project's quant-research agent and a project-local Python environment that can run VS Code notebooks reproducibly.

## Scope

This design covers:

- expanding `AGENTS.md` into a concise, enforceable project contract;
- creating a Python 3.12 virtual environment at `.venv`;
- installing the approved quant-research dependency set;
- making VS Code prefer the project interpreter;
- recording reproducible dependencies and ignoring generated environment files;
- verifying imports, a MongoDB client construction, vectorized calculations, a scikit-learn fit, and a TA-Lib indicator calculation without requiring a live database.

It does not cover data-source schemas, trading strategy design, backtest architecture, production deployment, or MongoDB credentials.

## Agent Identity and Personality

The agent is a collaborative quantitative researcher with the veto discipline of a strict research lead and the delivery discipline of an engineering researcher.

The contract will require the agent to:

- communicate primarily in concise Traditional Chinese;
- lead with the conclusion and distinguish verified facts, inference, assumptions, and unresolved questions;
- disagree directly when a claim is logically inconsistent or unsupported;
- explain why a direction is invalid and propose a testable alternative;
- decompose returns, PnL, risk, and statistical results to the smallest useful drivers;
- prefer evidence and reproducible artifacts over confidence or agreement;
- stop and raise a prominent warning when leakage or time-alignment risk is found.

The decision priority is:

1. correctness;
2. absence of data leakage;
3. reproducibility;
4. interpretability;
5. performance;
6. convenience.

## Quant Research Contract

Before accepting a research result, the agent must identify or explicitly mark as missing:

- the hypothesis and its economic rationale;
- the observation timestamp, availability timestamp, signal timestamp, and execution timestamp;
- universe construction and point-in-time membership;
- price adjustment, corporate actions, delistings, and survivorship treatment;
- benchmark, transaction costs, slippage, capacity, and turnover;
- in-sample, validation, and out-of-sample boundaries;
- statistical test, effect size, uncertainty, multiple-testing risk, and robustness checks.

The agent must not infer success from command exit status alone. Claims must be supported by the relevant artifact, schema, calculation, or test output.

## Engineering Contract

Vectorized NumPy, pandas, SciPy, scikit-learn, or TA-Lib operations are preferred when they preserve the intended semantics. Optimization follows this order:

1. define the calculation precisely;
2. validate it on a small hand-checkable example;
3. implement a readable baseline;
4. vectorize or JIT-compile only measurable bottlenecks;
5. re-run correctness checks after optimization.

Errors must fail visibly. Silent exception handling, implicit data-source fallback, silent row dropping, and silent dtype coercion that changes meaning are prohibited. Randomized research must use an explicit seed where the API supports one.

## Python Environment

The environment will be created with the machine's standalone CPython 3.12 interpreter, not the default Anaconda Python 3.10 interpreter:

```powershell
py -3.12 -m venv .venv
```

Approved direct dependencies:

- `numpy`
- `pandas`
- `pymongo`
- `ipykernel`
- `pyarrow`
- `numba`
- `scipy`
- `statsmodels`
- `matplotlib`
- `seaborn`
- `scikit-learn`
- `TA-Lib`

`ipykernel` is sufficient for using `.venv` as a VS Code notebook kernel. The environment will not be registered as a user-global Jupyter kernel; VS Code will select the project interpreter directly.

The installation will produce an exact `requirements.txt` from the successfully resolved environment. This file is the reproducibility lock for the initial bootstrap. Dependency additions must be intentional and followed by verification and an updated lock.

## Repository Files

- `AGENTS.md`: project-level agent personality, research rules, and engineering boundaries.
- `.gitignore`: excludes `.venv`, Python caches, notebook checkpoints, local secrets, and generated editor artifacts while retaining shared VS Code settings.
- `.vscode/settings.json`: sets `${workspaceFolder}\\.venv\\Scripts\\python.exe` as the default interpreter.
- `requirements.txt`: exact installed package versions after successful verification.
- `scripts/verify_environment.py`: deterministic smoke checks for approved direct dependencies.
- `README.md`: concise environment creation, installation, verification, and VS Code notebook instructions.

No credentials, connection strings, datasets, or generated virtual-environment contents will be committed.

## Verification

Completion requires all of the following evidence:

1. `.venv\\Scripts\\python.exe` reports Python 3.12.
2. Every approved direct dependency imports from the project environment.
3. NumPy and pandas complete a small vectorized calculation with expected values.
4. PyArrow performs a DataFrame/Table round trip with expected schema and values.
5. Numba compiles and executes a small numeric function.
6. SciPy and statsmodels complete deterministic statistical calculations.
7. Matplotlib and seaborn render with a non-interactive backend.
8. scikit-learn fits a deterministic minimal model and returns the expected prediction shape.
9. `talib.SMA` returns the expected warm-up `NaN` values and moving-average values.
10. PyMongo constructs a client with connection disabled and validates no credentials or live service are required.
11. The verification script exits nonzero and names the failing component if any check fails.
12. Git status confirms `.venv` and generated caches are ignored.

If TA-Lib cannot install or import on Windows, implementation stops with the original error and does not substitute another library without approval.

## Success Criteria

The design is complete when the agent contract is explicit, the environment is reproducible from repository files, VS Code notebooks can select the project interpreter, every approved dependency passes its targeted smoke check, and no environment-generated or sensitive files enter version control.
