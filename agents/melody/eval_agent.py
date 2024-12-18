import copy
import torch
import random

import torch.nn.functional as F

from config import (
    CHORD_SIZE_MELODY,
    DURATION_SIZE_MELODY,
    PITCH_SIZE_MELODY,
    INT_TO_TRIAD,
    PITCH_VECTOR_SIZE,
)
from ..utils import select_with_preference


def predict_next_notes(
    chord_sequence, melody_agent, melody_primer, config
) -> list[list[int]]:
    with torch.no_grad():
        all_notes: list[list[int]] = []
        if config["SCALE_MELODY"]:
            pitch_preferences: list[int] = generate_scale_preferences(config)
        if config["BAD_COMS"]:
            for i in range(len(chord_sequence)):
                root = random.randint(0, 11)
                chord_type = random.randint(0, 1)
                chord = (
                    [root, root + 4, root + 7]
                    if chord_type
                    else [root, root + 3, root + 7]
                )
                chord_sequence[i] = (chord, random.randint(1, 4))

        running_time_on_chord_beats: float = 0

        current_chord_duration_beats = chord_sequence[0][1]
        next_current_chord = get_chord_tensor(chord_sequence[0][0])
        try:
            next_next_chord = get_chord_tensor(chord_sequence[1][0])
        except:
            next_next_chord = next_current_chord

        (
            pitches,
            durations,
            current_chords,
            next_chords,
            current_chord_time_lefts,
            accumulated_times,
        ) = get_tensors(melody_primer)

        chord_num: int = 0
        accumulated_time: int = 0

        sum_duration_in_beats: float = 0.0
        print("Generating melody uding ", str(melody_agent))
        while True:
            x = torch.cat(
                (
                    pitches,
                    durations,
                    current_chords,
                    next_chords,
                    current_chord_time_lefts,
                ),
                dim=1,
            )

            # add batch dimension
            x = x.unsqueeze(0)
            accumulated_times = accumulated_times.unsqueeze(0)
            current_chord_time_lefts = current_chord_time_lefts.unsqueeze(0)

            pitch_logits, duration_logits = melody_agent(
                x, accumulated_times, current_chord_time_lefts
            )
            note_probabilities = F.softmax(pitch_logits, dim=1).view(-1)
            duration_probabilities = F.softmax(duration_logits, dim=1).view(-1)

            if config["SCALE_MELODY"] and not config["FULL_SCALE_MELODY"]:
                note_probabilities = select_with_preference(
                    note_probabilities, pitch_preferences
                )

            if config["DURATION_PREFERENCES_MELODY"]:
                duration_probabilities = select_with_preference(
                    duration_probabilities, config["DURATION_PREFERENCES_MELODY"]
                )

            note_probabilities = apply_temperature(
                note_probabilities, config["NOTE_TEMPERATURE_MELODY"]
            )

            duration_probabilities = apply_temperature(
                duration_probabilities, config["DURATION_TEMPERATURE_MELODY"]
            )

            # Sample from the distributions
            next_note = torch.multinomial(note_probabilities, 1).unsqueeze(1)
            next_duration = torch.multinomial(duration_probabilities, 1).unsqueeze(1)
            duration_in_quarter_notes: float = next_duration.item() + 1

            sum_duration_in_beats += duration_in_quarter_notes / 4
            running_time_on_chord_beats += duration_in_quarter_notes / 4

            accumulated_time += duration_in_quarter_notes

            all_notes.append([next_note.item() + 61, duration_in_quarter_notes])

            next_accumulated_time = get_accumulated_time_tensor(accumulated_time)

            # We are done
            if sum_duration_in_beats >= config["LENGTH"] * 4:
                all_notes.pop()
                # Add the last note, with the remaining duration
                all_notes.append(
                    [
                        next_note.item() + 61,
                        int(
                            (
                                config["LENGTH"] * 4
                                - (
                                    sum_duration_in_beats
                                    - (duration_in_quarter_notes / 4)
                                )
                            )
                            * 4
                        ),
                    ]
                )
                break

            while running_time_on_chord_beats > current_chord_duration_beats:
                chord_num += 1
                if chord_num >= len(chord_sequence):
                    break

                running_time_on_chord_beats -= current_chord_duration_beats
                try:
                    current_chord_duration_beats = chord_sequence[chord_num][1]
                # If no more chord, set duration to 4 beats
                except:
                    current_chord_duration_beats = 1

                try:
                    next_current_chord: torch.Tensor = get_chord_tensor(
                        chord_sequence[chord_num][0]
                    )
                    next_next_chord: torch.Tensor = get_chord_tensor(
                        chord_sequence[chord_num + 1][0]
                    )
                # If there are no more chords, current chord is set as next chord
                except:
                    next_current_chord: torch.Tensor = get_chord_tensor(
                        chord_sequence[chord_num][0]
                    )

                    next_next_chord: torch.Tensor = next_current_chord

            next_pitch_vector, next_duration_vector = get_pitch_duration_tensor(
                next_note.item(), (next_duration.item())
            )

            next_current_chord_time_lefts = get_time_left_on_chord_tensor(
                current_chord_duration_beats, running_time_on_chord_beats
            )

            # Check if the current note is end or start of bar (With 1/8 note threshold)
            # if (running_time_total_beats / 4) % 4 < 0.125:
            #     is_start_of_bar: bool = True
            # else:
            #     is_start_of_bar: bool = False
            # if (running_time_total_beats / 4) % 4 > 0.875:
            #     is_end_of_bar: bool = True
            # else:
            #     is_end_of_bar: bool = False

            # bars: torch.Tensor = torch.tensor([is_start_of_bar, is_end_of_bar])

            (
                pitches,
                durations,
                current_chords,
                next_chords,
                accumulated_times,
                current_chord_time_lefts,
            ) = update_input_tensors(
                pitches,
                durations,
                current_chords,
                next_chords,
                accumulated_times,
                current_chord_time_lefts,
                next_pitch_vector,
                next_duration_vector,
                next_current_chord,
                next_next_chord,
                next_accumulated_time,
                next_current_chord_time_lefts,
            )

    return all_notes


def get_tensors(melody_primer):
    pitches = []
    durations = []
    current_chords = []
    next_chords = []
    current_chord_time_lefts = []
    accumulated_times = []

    for note in melody_primer:
        # Convert each item to a tensor before appending
        pitches.append(torch.tensor(note[0]))
        durations.append(torch.tensor(note[1]))
        current_chords.append(torch.tensor(note[2]))
        next_chords.append(torch.tensor(note[3]))
        current_chord_time_lefts.append(torch.tensor(note[4]))
        accumulated_times.append(torch.tensor(note[5]))

    pitches = torch.stack(pitches)
    durations = torch.stack(durations)
    current_chords = torch.stack(current_chords)
    next_chords = torch.stack(next_chords)
    current_chord_time_lefts = torch.stack(current_chord_time_lefts)
    accumulated_times = torch.stack(accumulated_times)

    return (
        pitches,
        durations,
        current_chords,
        next_chords,
        current_chord_time_lefts,
        accumulated_times,
    )


def update_input_tensors(
    pitches,
    durations,
    current_chords,
    next_chords,
    accumulated_times,
    current_chord_time_lefts,
    next_pitch_vector,
    next_duration_vector,
    next_current_chord,
    next_next_chord,
    next_accumulated_time,
    next_current_chord_time_lefts,
):
    pitches = torch.cat((pitches, next_pitch_vector.unsqueeze(0)), dim=0)
    durations = torch.cat((durations, next_duration_vector.unsqueeze(0)), dim=0)
    current_chords = torch.cat((current_chords, next_current_chord.unsqueeze(0)), dim=0)
    next_chords = torch.cat((next_chords, next_next_chord.unsqueeze(0)), dim=0)

    current_chord_time_lefts = torch.cat(
        (
            current_chord_time_lefts.squeeze(0),
            next_current_chord_time_lefts.unsqueeze(0),
        ),
        dim=0,
    )

    accumulated_times = torch.cat(
        (accumulated_times.squeeze(0), next_accumulated_time.unsqueeze(0)), dim=0
    )

    pitches = pitches[1:]
    durations = durations[1:]
    current_chords = current_chords[1:]
    next_chords = next_chords[1:]
    accumulated_times = accumulated_times[1:]
    current_chord_time_lefts = current_chord_time_lefts[1:]

    # print("pitch", get_one_hot_index(pitches[-1]))
    # print("duration", get_one_hot_index(durations[-1]))
    # print("current_chord", get_one_hot_index(current_chords[-1]))
    # print("next_chord", get_one_hot_index(next_chords[-1]))
    # print("accumulated_times", get_one_hot_index(accumulated_times[-1]))
    # print("current_chord_time_lefts", get_one_hot_index(current_chord_time_lefts[-1]))

    # print("")

    return (
        pitches,
        durations,
        current_chords,
        next_chords,
        accumulated_times,
        current_chord_time_lefts,
    )


def get_one_hot_index(one_hot_list: list[int]) -> int:
    """
    Gets the index of the one hot encoded list. For debugging.

    Args:
    ----------
        one_hot_list (list[int]): one hot encoded list

    Returns:
    ----------
        int: index of the one hot encoded list
    """
    return next((i for i, value in enumerate(one_hot_list) if value == 1), None)


def get_time_left_on_chord_tensor(
    current_chord_duration_beats: int, running_time_on_chord_beats: float
) -> torch.Tensor:
    time_left_on_chord: float = (
        current_chord_duration_beats - running_time_on_chord_beats
    ) * 4

    time_left_vector: list[int] = [0] * 16

    time_left_on_chord = min(time_left_on_chord, 15)
    time_left_on_chord = max(time_left_on_chord, 0)

    time_left_vector[int(time_left_on_chord)] = 1

    return torch.tensor(time_left_vector)


def get_accumulated_time_tensor(
    accumulated_bars: int,
) -> torch.Tensor:
    index: int = int(accumulated_bars % 4)

    accumulated_list = [0, 0, 0, 0]
    accumulated_list[index] = 1
    return torch.tensor(accumulated_list)


def apply_temperature(logits, temperature):
    # Adjust the logits by the temperature
    return logits / temperature


def get_chord_tensor(chord: list[int]) -> torch.Tensor:
    """
    One hot encodes a chord into a tensor list.
    Args:
        chord (list[int]): traid chord in form [note, note, note]

    Returns:
        torch.Tensor: one hot encoded list of chord
    """
    root_note: int = chord[0]
    chord_type: list[int] = [c - root_note for c in chord]

    chord_type: int = get_key(chord_type, INT_TO_TRIAD)
    chord_index: int = root_note * 2 + chord_type

    chord_vector = [0] * CHORD_SIZE_MELODY

    try:
        chord_vector[chord_index] = 1
    except:
        print("Chord index", chord_index)
        print("Chord", chord)
        print("Root note", root_note)
        print("Chord type", chord_type)
        print("Chord vector", chord_vector)
    return torch.tensor(chord_vector)


# Function to find key from value
def get_key(val, dic):
    for key, value in dic.items():
        if value == val:
            return key
    return "Key not found"


def get_pitch_duration_tensor(
    pitch: int, duration: int
) -> [torch.Tensor, torch.Tensor]:
    pitch_vector = [0] * PITCH_SIZE_MELODY
    pitch_vector[pitch] = 1
    duration_vector = [0] * DURATION_SIZE_MELODY
    duration_vector[duration] = 1
    return torch.tensor(pitch_vector), torch.tensor(duration_vector)


def generate_scale_preferences(config) -> list[int]:
    if config["SCALE_MELODY"] == "major pentatonic":
        intervals = [0, 2, 4, 7, 9]
    if config["SCALE_MELODY"] == "major scale":
        intervals = [0, 2, 4, 5, 7, 9, 11]
    full_range = []

    # Iterate through all MIDI notes
    for midi_note in range(PITCH_VECTOR_SIZE):  # MIDI notes range from 0 to 127
        # Check if the note is in the correct scale
        if midi_note % 12 in intervals:
            note_index = midi_note - 1
            if note_index > 0:
                full_range.append(note_index)
    # for pause
    if not config["NO_PAUSE"]:
        full_range.append(PITCH_VECTOR_SIZE)

    return full_range
