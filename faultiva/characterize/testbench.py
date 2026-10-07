"""Generate a SystemVerilog fault-campaign testbench from a CircuitConfig.

STRUCTURE borrowed from the V2.2 campaign's common_testbench(): one module
that reads vectors from .mem files, drives the DUT through a `run_transaction`
task, and writes one CSV row per (fault, vector) carrying the four observable
channels.  The V2.2 version supplied `declarations` and `task` per circuit by
hand; here both are synthesized from the validated config.

WHAT THE GENERATED TESTBENCH DOES

    MODE=VALIDATE   fault-free only; replay twice and require byte equality,
                    which catches non-determinism before any fault is trusted
    MODE=BASELINE   fault-free only; this IS the golden reference for a
                    third-party circuit (no independent software oracle)
    MODE=CAMPAIGN   per batch: baseline, then every site x {SA0, SA1}

CSV COLUMNS (one row per transaction)
    circuit, mode, batch_id, run_type, site, stuck, vector,
    cycles, timed_out, protocol_error, unknown, response

`response` is zero-extended to 512 bits so one column width serves any output
up to that size; the campaign runner truncates to the circuit's real width.

RESERVED NAMES
    Every identifier the testbench declares for itself is tb_-prefixed, so
    a DUT port may be called `mode`, `site`, `rows` or anything else
    without colliding.  Only the fi_enable_i / fi_site_onehot_i /
    fi_stuck_value_i injection ports are genuinely reserved, and the config
    validator rejects those up front.

CONSTANT PINS
    Config inputs listed under `constants` are declared with their held value,
    wired to the DUT, and re-asserted at the start of every transaction so a
    reset cannot leave a mode select in an unintended state.  They never change
    during a campaign, so they cannot interact with fault-injection timing.

TIMING MODEL (simple_handshake)
    reset -> drive inputs -> pulse or raise `start` -> poll `done` until
    asserted or cycle_budget is exhausted -> sample output.

    timed_out       `done` never asserted within cycle_budget
    protocol_error  `done` already asserted before `start` - the design was
                    not idle, so the transaction is not trustworthy
    unknown         X or Z seen on `done` or the output while sampling
"""
from __future__ import annotations

from .config import CircuitConfig

CSV_FIELDS = ("circuit", "mode", "batch_id", "run_type", "site", "stuck",
              "vector", "cycles", "timed_out", "protocol_error", "unknown",
              "response")

RESPONSE_BITS = 512          # fixed CSV column width
RESPONSE_HEX = RESPONSE_BITS // 4


def _sv_path(path) -> str:
    """Forward slashes for $readmemh, which dislikes backslashes."""
    return str(path).replace("\\", "/")


def _active(signal) -> tuple[str, str]:
    """Return (asserted, deasserted) literals for a possibly active-low signal."""
    return ("0", "1") if signal.active_low else ("1", "0")


def declarations(cfg: CircuitConfig) -> str:
    """DUT signal declarations and the instantiation itself."""
    lines: list[str] = []

    rst_assert, _ = _active(cfg.reset)
    lines.append(f"logic {cfg.reset.name} = {rst_assert};")
    lines.append(f"logic {cfg.start.name} = "
                 f"{'0' if not cfg.start.active_low else '1'};")
    lines.append(f"logic {cfg.done.name};")

    for port in cfg.inputs:
        w = f"[{port.width - 1}:0] " if port.width > 1 else ""
        lines.append(f"logic {w}{port.name};")
        lines.append(f"logic {w}{port.name}_vectors[0:VECTOR_COUNT-1];")

    for const in cfg.constants:
        w = f"[{const.width - 1}:0] " if const.width > 1 else ""
        lines.append(f"logic {w}{const.name} = {const.width}'h{const.value:x};")

    ow = f"[{cfg.output.width - 1}:0] " if cfg.output.width > 1 else ""
    lines.append(f"logic {ow}{cfg.output.name};")

    conns = [f".{cfg.clock}(clk)", f".{cfg.reset.name}({cfg.reset.name})",
             f".{cfg.start.name}({cfg.start.name})",
             f".{cfg.done.name}({cfg.done.name})"]
    conns += [f".{p.name}({p.name})" for p in cfg.inputs]
    conns += [f".{c.name}({c.name})" for c in cfg.constants]
    conns.append(f".{cfg.output.name}({cfg.output.name})")
    conns += [".fi_enable_i(fi_enable)", ".fi_site_onehot_i(fi_onehot)",
              ".fi_stuck_value_i(fi_stuck)"]

    lines.append(f"{cfg.top} dut (")
    for index, conn in enumerate(conns):
        comma = "," if index < len(conns) - 1 else ""
        lines.append(f"    {conn}{comma}")
    lines.append("  );")
    return "\n  ".join(lines)


def setup(cfg: CircuitConfig, memory_dir) -> str:
    """$readmemh calls that load the stimulus for every input port."""
    return "\n".join(
        f'    $readmemh("{_sv_path(memory_dir)}/{p.name}.mem",{p.name}_vectors);'
        for p in cfg.inputs)


def transaction_task(cfg: CircuitConfig) -> str:
    """The run_transaction task: one transaction, four channels out."""
    rst_assert, rst_release = _active(cfg.reset)
    st_assert, st_idle = _active(cfg.start)
    done_hi = f"!{cfg.done.name}" if cfg.done.active_low else cfg.done.name
    done_lo = cfg.done.name if cfg.done.active_low else f"!{cfg.done.name}"

    drive = "\n      ".join(f"{p.name} = {p.name}_vectors[vector];"
                            for p in cfg.inputs)

    if cfg.start.pulse:
        kick = (f"@(negedge clk); {cfg.start.name} = {st_assert};\n"
                f"      @(negedge clk); {cfg.start.name} = {st_idle};")
    else:
        kick = f"@(negedge clk); {cfg.start.name} = {st_assert};"

    watched = "{" + f"{cfg.done.name},{cfg.output.name}" + "}"
    hold = ("\n      ".join(f"{c.name} = {c.width}'h{c.value:x};"
                            for c in cfg.constants)
            if cfg.constants else "")

    return f"""task automatic run_transaction(
      input integer vector, input logic inject, input integer selected,
      input logic forced,
      output integer cycles, output logic timed_out,
      output logic protocol_error, output logic unknown_seen,
      output logic [{RESPONSE_BITS - 1}:0] response);
    begin
      {cfg.start.name} = {st_idle}; fi_enable = 0; fi_onehot = '0;
      fi_stuck = forced;
      cycles = 0; timed_out = 0; protocol_error = 0; unknown_seen = 0;
      response = '0;
      {hold}

      // full reset before every transaction keeps vectors independent
      {cfg.reset.name} = {rst_assert};
      repeat (5) @(posedge clk);
      @(negedge clk);
      {cfg.reset.name} = {rst_release};
      repeat (2) @(posedge clk);

      // a design asserting done while idle cannot be trusted
      if ({done_hi}) protocol_error = 1;

      {drive}
      fi_enable = inject; fi_stuck = forced;
      if (inject) fi_onehot[selected] = 1'b1;

      {kick}

      while ({done_lo} && cycles < CYCLE_BUDGET) begin
        @(posedge clk); #1;
        cycles = cycles + 1;
        if ($isunknown({watched})) unknown_seen = 1;
      end

      timed_out = {done_lo};
      if (!timed_out) response = {{{RESPONSE_BITS}'b0 | {cfg.output.name}}};
      if ($isunknown({watched})) unknown_seen = 1;

      {cfg.start.name} = {st_idle}; fi_enable = 0; fi_onehot = '0;
      @(posedge clk);
    end
  endtask"""


def generate_testbench(cfg: CircuitConfig, site_count: int, memory_dir) -> str:
    """Emit the complete testbench source."""
    if site_count < 1:
        raise ValueError("site_count must be >= 1")
    module = f"tb_faultiva_{cfg.circuit}"
    header = ",".join(CSV_FIELDS)

    return f"""// Generated by faultiva.characterize -- do not edit by hand.
// circuit : {cfg.circuit}
// top     : {cfg.top}
// protocol: {cfg.protocol}
// vectors : {cfg.vectors}   sites: {site_count}   budget: {cfg.cycle_budget} cycles
`timescale 1ns/1ps

module {module};
  localparam integer VECTOR_COUNT = {cfg.vectors};
  localparam integer SITE_COUNT   = {site_count};
  localparam integer CYCLE_BUDGET = {cfg.cycle_budget};

  logic clk = 0;
  always #5 clk = ~clk;

  logic fi_enable = 0, fi_stuck = 0;
  logic [SITE_COUNT-1:0] fi_onehot = '0;

  {declarations(cfg)}

  integer tb_csv_fd, tb_vi, tb_site, tb_stuck, tb_batch_id;
  integer tb_site_start, tb_site_count;
  integer tb_failures = 0, tb_rows = 0;
  integer cycles_result;
  logic timeout_result, protocol_result, unknown_result;
  logic [{RESPONSE_BITS - 1}:0] response_result;
  string tb_mode, tb_csv_path;

  {transaction_task(cfg)}

  task automatic emit(input string run_type, input integer site_value,
                      input integer stuck_value);
  begin
    $fwrite(tb_csv_fd,
      "{cfg.circuit},%s,%0d,%s,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0{RESPONSE_HEX}h\\n",
      tb_mode, tb_batch_id, run_type, site_value, stuck_value, tb_vi,
      cycles_result, timeout_result, protocol_result, unknown_result,
      response_result);
    tb_rows = tb_rows + 1;
  end endtask

  task automatic sweep_baseline();
  begin
    for (tb_vi = 0; tb_vi < VECTOR_COUNT; tb_vi = tb_vi + 1) begin
      run_transaction(tb_vi, 0, 0, 0, cycles_result, timeout_result,
                      protocol_result, unknown_result, response_result);
      emit("BASELINE", -1, -1);
      if (unknown_result || protocol_result) tb_failures = tb_failures + 1;
    end
  end endtask

  initial begin
{setup(cfg, memory_dir)}
    if (!$value$plusargs("MODE=%s", tb_mode)) $fatal(1, "MODE plusarg required");
    if (!$value$plusargs("CSV=%s", tb_csv_path)) $fatal(1, "CSV plusarg required");
    tb_batch_id = -1; tb_site_start = 0; tb_site_count = 0;
    void'($value$plusargs("BATCH_ID=%d", tb_batch_id));
    void'($value$plusargs("SITE_START=%d", tb_site_start));
    void'($value$plusargs("SITE_COUNT=%d", tb_site_count));

    tb_csv_fd = $fopen(tb_csv_path, "w");
    if (tb_csv_fd == 0) $fatal(1, "CSV open failed: %s", tb_csv_path);
    $fdisplay(tb_csv_fd, "{header}");

    if (tb_mode == "VALIDATE" || tb_mode == "BASELINE") begin
      sweep_baseline();
      $fclose(tb_csv_fd);
      $display("FAULTIVA_CIRCUIT={cfg.circuit} ROWS=%0d", tb_rows);
      if (tb_failures == 0) begin
        $display("FAULTIVA_%s_RESULT=PASS", tb_mode);
        $finish;
      end
      else $fatal(1, "FAULTIVA_%s_RESULT=FAIL failures=%0d", tb_mode, tb_failures);
    end
    else if (tb_mode == "CAMPAIGN") begin
      if (tb_site_count < 1) $fatal(1, "SITE_COUNT required for CAMPAIGN");
      if (tb_site_start + tb_site_count > SITE_COUNT)
        $fatal(1, "batch exceeds SITE_COUNT");
      sweep_baseline();
      for (tb_site = tb_site_start; tb_site < tb_site_start + tb_site_count;
           tb_site = tb_site + 1)
        for (tb_stuck = 0; tb_stuck < 2; tb_stuck = tb_stuck + 1)
          for (tb_vi = 0; tb_vi < VECTOR_COUNT; tb_vi = tb_vi + 1) begin
            run_transaction(tb_vi, 1, tb_site, tb_stuck, cycles_result,
                            timeout_result, protocol_result, unknown_result,
                            response_result);
            emit("ENABLED", tb_site, tb_stuck);
          end
      $fclose(tb_csv_fd);
      $display("FAULTIVA_CIRCUIT={cfg.circuit} BATCH=%0d ROWS=%0d",
               tb_batch_id, tb_rows);
      $display("FAULTIVA_CAMPAIGN_RESULT=PASS");
      $finish;
    end
    else $fatal(1, "Unknown MODE: %s", tb_mode);
  end
endmodule
"""


def expected_rows(cfg: CircuitConfig, batch_sites: int) -> int:
    """Rows a CAMPAIGN batch must emit: baseline + every fault x every vector."""
    return cfg.vectors + batch_sites * 2 * cfg.vectors
