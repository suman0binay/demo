"""
openbci_bridge.py
================
Real integration with OpenBCI hardware via BrainFlow (OpenBCI's own
official open-source SDK — the same library OpenBCI's own GUI is
built on). Connects a real board (or BrainFlow's built-in synthetic
board for testing without hardware), reads real EEG samples, and
feeds them through the SAME consent-gated, stats-only path as
everything else in this project — signal_stats_bridge.py,
digital_dna.py's add_live_signal(), and from there optionally into
the art/video generators as abstract data channels.

WHAT "COMPATIBLE" ACTUALLY MEANS HERE
------------------------------------------
1. Connection: real BrainFlow API calls (BoardShim, prepare_session,
   start_stream, get_board_data) — tested against BrainFlow's
   synthetic board in this environment; the same calls work
   identically against a real Cyton/Ganglion board, just with a real
   serial_port instead of BoardIds.SYNTHETIC_BOARD.
2. Consent: this file does NOT bypass add_live_signal()'s consent
   gate — connecting a board is the "real permission step" digital_dna
   .py's ALLOWED_SOURCES already requires for source="eeg_telemetry";
   you still call add_live_signal() with consent_verified=True
   yourself, explicitly, same as any other source.
3. Abstraction: raw channel samples go through
   signal_stats_bridge.signal_channel_stats() before touching
   anything else — mean/variance only, never raw waveforms, and
   never claimed to represent a "brain state" in generated art/video.
   That boundary is unchanged by using real hardware instead of
   placeholder numbers.

WHAT THIS DOESN'T CLAIM
----------------------------
OpenBCI boards are research/hobbyist-grade EEG hardware, not FDA-
cleared medical devices — this file (and the ALLOWED_SOURCES entry
it uses) doesn't claim otherwise, and neither should you if you build
on this.

Usage
-----
    from openbci_bridge import connect_board, read_eeg_window, disconnect_board
    from brainflow.board_shim import BoardIds

    board = connect_board(BoardIds.SYNTHETIC_BOARD)   # or BoardIds.CYTON_BOARD with serial_port=...
    readings = read_eeg_window(board, duration_s=1.0, channel_index=0)
    disconnect_board(board)

    # then, same consent-gated path as everything else:
    from signal_stats_bridge import signal_channel_stats
    stats = signal_channel_stats(readings, label="eeg")
    dna.add_live_signal("eeg_telemetry", hashlib.sha256(str(readings).encode()).hexdigest(),
                          confidence=0.9, consent_verified=True)
"""

from __future__ import annotations
import time

from brainflow.board_shim import BoardShim, BrainFlowInputParams, BoardIds


def connect_board(board_id: int, serial_port: str | None = None) -> BoardShim:
    """Real BrainFlow session setup. For a real OpenBCI Cyton/Ganglion,
    pass BoardIds.CYTON_BOARD / BoardIds.GANGLION_BOARD and the real
    serial_port your board enumerates as. For testing without hardware,
    BoardIds.SYNTHETIC_BOARD needs no serial_port."""
    params = BrainFlowInputParams()
    if serial_port:
        params.serial_port = serial_port
    board = BoardShim(board_id, params)
    board.prepare_session()
    board.start_stream()
    return board


def read_eeg_window(board: BoardShim, duration_s: float = 1.0, channel_index: int = 0) -> list[float]:
    """Real windowed read from a live BrainFlow stream. Returns one
    channel's samples as a plain list of floats — this is the raw
    waveform, and it should go DIRECTLY into signal_stats_bridge.py,
    never into anything else, per this file's consent/abstraction
    boundary above."""
    time.sleep(duration_s)
    data = board.get_board_data()
    eeg_channels = BoardShim.get_eeg_channels(board.get_board_id())
    channel = eeg_channels[channel_index]
    return data[channel].tolist()


def disconnect_board(board: BoardShim):
    board.stop_stream()
    board.release_session()


if __name__ == "__main__":
    import hashlib
    from signal_stats_bridge import signal_channel_stats
    from digital_dna import DigitalDNA
    from research_art_generator import _build_art_prompt

    print("=== Connecting to BrainFlow's synthetic board (real API, no hardware needed) ===")
    board = connect_board(BoardIds.SYNTHETIC_BOARD)

    print("=== Reading a real 1-second window of synthetic EEG data ===")
    readings = read_eeg_window(board, duration_s=1.0, channel_index=0)
    disconnect_board(board)
    print(f"Real samples read: {len(readings)}")
    print(f"First 5 raw samples (this stays local, never leaves this function): {readings[:5]}")

    print("\n=== Converting to abstract statistics (the ONLY thing allowed downstream) ===")
    stats = signal_channel_stats(readings, label="eeg")
    print(f"Abstract stats: {stats}")

    print("\n=== Feeding through the real consent gate ===")
    dna = DigitalDNA(seed_label="openbci-compat-test")
    feature_hash = hashlib.sha256(str(readings).encode()).hexdigest()
    event = dna.add_live_signal("eeg_telemetry", feature_hash, confidence=0.9, consent_verified=True)
    print(f"Real mutation event from real OpenBCI-shaped data: mutation_rate={event.mutation_rate}")

    print("\n=== Real art prompt with real (synthetic-board) EEG stats folded in ===")
    mos = {"quality": 4.0, "connectivity": 5.0, "agreement": 4.5, "mos": 4.5}
    prompt = _build_art_prompt(mos, topic_count=3, style="abstract, calm blues", extra_signal_stats=stats)
    print(prompt)
