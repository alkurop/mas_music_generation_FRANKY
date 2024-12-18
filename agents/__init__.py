from .bass import (
    Bass_Network,
    Bass_Network_LSTM,
    train_bass,
    predict_next_k_notes_bass,
    play_bass,
)

from .chord import (
    Chord_Network,
    Chord_LSTM_Network,
    train_chord,
    play_chord,
    predict_next_k_notes_chords,
)

from .melody import (
    train_melody,
    Melody_Network,
    generate_scale_preferences,
    select_with_preference,
)

from .harmony import play_harmony

from .drum import train_drum

from .coplay import play_agents

from .create_agents import create_agents

from .utils import (
    select_with_preference,
    beats_to_seconds,
    seconds_to_beat,
    adjust_for_key,
)

from .eval_all_agents import eval_all_agents
