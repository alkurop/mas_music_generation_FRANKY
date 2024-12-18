import json
import torch
import numpy as np
import torch.nn as nn
import torch.optim as optim
from torch.utils.data import Dataset, DataLoader
import matplotlib.pyplot as plt

import tensorflow as tf

from .melody_network import Melody_Network
from data_processing import Melody_Dataset


from config import (
    NUM_EPOCHS_MELODY,
    LEARNING_RATE_MELODY,
    BATCH_SIZE_MELODY,
    MODEL_PATH_MELODY,
    MODEL_NON_COOP_PATH_MELODY,
    DEVICE,
    PITCH_SIZE_MELODY,
    DURATION_SIZE_MELODY,
    ALPHA1_MELODY,
    ALPHA2_MELODY,
    TRAIN_DATASET_PATH_MELODY,
    VAL_DATASET_PATH_MELODY,
    MAX_BATCHES_MELODY,
    WEIGHT_DECAY_MELODY,
    COMMENT_MELODY,
    PITCH_VECTOR_SIZE,
    SEQUENCE_LENGHT_MELODY,
    CHORD_SIZE_MELODY,
    HIDDEN_SIZE_LSTM_MELODY,
    COMBINED,
    TRAIN_DATASET_COMBINED_PATH_MELODY,
    VAL_DATASET_COMBINED_PATH_MELODY,
    CHECKPOINT_FREQUENCY_MELODY,
)


def train_melody(model: Melody_Network) -> None:
    """
    Trains the MelodyGenerator model on the provided dataset.

    Args:
        model (MelodyGenerator): The neural network model to train.
        dataset (Dataset): The dataset to train the model on.


    Returns:
        list: A list of average epoch losses for each epoch.
    """

    if COMBINED:
        melody_dataset_train = torch.load(TRAIN_DATASET_COMBINED_PATH_MELODY)
        melody_dataset_val = torch.load(VAL_DATASET_COMBINED_PATH_MELODY)
    else:
        melody_dataset_train = torch.load(TRAIN_DATASET_PATH_MELODY)
        melody_dataset_val = torch.load(VAL_DATASET_PATH_MELODY)

    print(len(melody_dataset_train))

    print(sum(p.numel() for p in model.parameters() if p.requires_grad))

    # Create DataLoader
    dataloader_train = DataLoader(
        melody_dataset_train,
        batch_size=BATCH_SIZE_MELODY,
        shuffle=True,
        collate_fn=process_data,
    )
    dataloader_val = DataLoader(
        melody_dataset_val,
        batch_size=BATCH_SIZE_MELODY,
        shuffle=True,
        collate_fn=process_data,
    )

    # Define loss function and optimizer
    criterion = nn.CrossEntropyLoss()
    optimizer = optim.Adam(
        model.parameters(), lr=LEARNING_RATE_MELODY, weight_decay=WEIGHT_DECAY_MELODY
    )

    loss_list = []
    val_loss_list = []

    # Training loop
    for epoch in range(NUM_EPOCHS_MELODY):
        batch_loss = []
        if epoch % CHECKPOINT_FREQUENCY_MELODY == 0:
            torch.save(
                model,
                "models/melody/checkpoints/checkpoint_"
                + (COMMENT_MELODY)
                + str(epoch)
                + ".pt",
            )
            # TODO: save data to json
            print("saving checkpoint")
        for idx, batch in enumerate(dataloader_train):
            if idx > MAX_BATCHES_MELODY:
                break
            (
                pitches,
                durations,
                current_chord,
                next_chord,
            ) = batch[0]
            gt_pitches, gt_durations = batch[1]
            accumulated_time = batch[2]
            time_left_on_chord = batch[3]

            gt_pitches.to(DEVICE)
            gt_durations.to(DEVICE)
            # Zero the parameter gradients
            optimizer.zero_grad()

            x = torch.cat(
                (
                    pitches,
                    durations,
                    current_chord,
                    next_chord,
                    time_left_on_chord,
                ),
                dim=2,
            )
            
            if "non_coop" in str(model):
                x = torch.cat(
                    (
                        pitches,
                        durations,
                    ),
                    dim=2,
                )

            pitch_logits, duration_logits = model(
                x, accumulated_time, time_left_on_chord
            )

            pitch_loss = criterion(
                pitch_logits, get_gt(gt_pitches.squeeze(1)).to(DEVICE)
            )
            duration_loss = criterion(
                duration_logits, get_gt(gt_durations.squeeze(1)).to(DEVICE)
            )

            loss = pitch_loss * ALPHA1_MELODY + duration_loss * ALPHA2_MELODY

            batch_loss.append(loss.item())

            # Backward pass and optimize
            loss.backward()
            optimizer.step()

        val_loss = get_validation_loss(model, dataloader_val, criterion)

        loss_list.append(np.mean(batch_loss))
        val_loss_list.append(val_loss)

        print(
            f"Epoch:  {epoch + 1} Loss: {round(loss_list[-1], 2)} Validation loss: {round(val_loss_list[-1],2)}"
        )

    # Save the model
    plot_loss(loss_list, val_loss_list, model)
    model_path = MODEL_NON_COOP_PATH_MELODY if "non_coop" in str(model) else MODEL_PATH_MELODY
    torch.save(model, model_path)

    save_to_json(loss_list, val_loss_list, model)

    plt.show()


def save_to_json(loss_list: list, val_loss_list: list, model: Melody_Network):
    """
    Save the training data, including hyperparameters, loss list, and validation loss list, to a JSON file.

    Args:
    ----------
        loss_list (list): List of training losses.
        val_loss_list (list): List of validation losses.
    """

    hyperparameters = {
        "PITCH_VECTOR_SIZE": PITCH_VECTOR_SIZE,
        "SEQUENCE_LENGHT_MELODY": SEQUENCE_LENGHT_MELODY,
        "CHORD_SIZE_MELODY": CHORD_SIZE_MELODY,
        "DURATION_SIZE_MELODY": DURATION_SIZE_MELODY,
        "NUM_EPOCHS_MELODY": NUM_EPOCHS_MELODY,
        "HIDDEN_SIZE_LSTM_MELODY": HIDDEN_SIZE_LSTM_MELODY,
        "ALPHA1_MELODY": ALPHA1_MELODY,
        "ALPHA2_MELODY": ALPHA2_MELODY,
        "LEARNING_RATE_MELODY": LEARNING_RATE_MELODY,
        "BATCH_SIZE_MELODY": BATCH_SIZE_MELODY,
        "MAX_BATCHES_MELODY": MAX_BATCHES_MELODY,
        "WEIGHT_DECAY_MELODY": WEIGHT_DECAY_MELODY,
        "COMBINED": COMBINED,
    }

    # Combine hyperparameters and training data into a single dictionary
    data_to_save = {
        "hyperparameters": hyperparameters,
        "loss_list": loss_list,
        "val_loss_list": val_loss_list,
    }

    # Save the combined data as a JSON file
    file_name = (
        "results/data/melody/"
        + str(model)
        + "/training_data"
        + str(hyperparameters["NUM_EPOCHS_MELODY"])
        + "_"
        + COMMENT_MELODY
        + ".json"
    )

    with open(file_name, "w") as file:
        json.dump(data_to_save, file, indent=4)


def get_validation_loss(model: nn.Module, dataloader: DataLoader, criterion) -> float:
    """
    Calculate the validation loss for a given model using the provided dataloader and criterion.

    Args:
    ----------
        model (nn.Module): The model to evaluate.
        dataloader (DataLoader): The dataloader containing the validation data.
        criterion: The loss criterion used for calculating the loss.

    Returns:
    ----------
        float: The average validation loss.
    """
    model.eval()
    batch_loss = []
    for idx, batch in enumerate(dataloader):
        if idx > MAX_BATCHES_MELODY / 10:
            break
        (
            pitches,
            durations,
            current_chord,
            next_chord,
        ) = batch[0]
        gt_pitches, gt_durations = batch[1]
        accumulated_time = batch[2]
        time_left_on_chord = batch[3]

        gt_pitches.to(DEVICE)
        gt_durations.to(DEVICE)

        x = torch.cat(
            (pitches, durations, current_chord, next_chord, time_left_on_chord),
            dim=2,
        )
        if "non_coop" in str(model):
            x = torch.cat((pitches, durations), dim=2)

        pitch_logits, duration_logits = model(x, accumulated_time, time_left_on_chord)

        pitch_loss = criterion(pitch_logits, get_gt(gt_pitches.squeeze(1)).to(DEVICE))
        duration_loss = criterion(
            duration_logits, get_gt(gt_durations.squeeze(1)).to(DEVICE)
        )

        loss = pitch_loss * ALPHA1_MELODY + duration_loss * ALPHA2_MELODY

        batch_loss.append(loss.item())

    model.train()
    return np.mean(batch_loss)


def get_gt(gt):
    """
    Converts a one-hot encoded ground truth tensor to its corresponding label tensor.

    Args:
    ----------
        gt (torch.Tensor): The ground truth tensor.

    Returns:
    ----------
        torch.Tensor: The label tensor obtained by taking the argmax along the second dimension of the input tensor.
    """
    if len(gt.size()) > 1 and gt.size(1) > 1:  # Check for one-hot encoding
        return torch.argmax(gt, dim=1)


def plot_loss(
    loss_values: list[float], val_loss_values: list[float], model: Melody_Network
) -> None:
    """
    Plots the training and validation loss over batches.

    Parameters
    ----------
    loss_values : list[float]
        A list of training loss values to be plotted.
    val_loss_values : list[float]
        A list of validation loss values to be plotted.

    Returns
    -------
    None
    """

    # Plot training loss
    plt.plot(loss_values, color="blue", label="Training Loss")

    # Plot validation loss
    plt.plot(val_loss_values, color="red", label="Validation Loss")

    # Add title and labels
    plt.title("Training and Validation Loss")
    plt.xlabel("Batch")
    plt.ylabel("Loss")

    # Add legend
    plt.legend()

    # Optional: Add grid for better readability
    plt.grid(True)

    # Save the plot
    plt.savefig(
        "figures/melody/"
        + str(model)
        + "/"
        + str(NUM_EPOCHS_MELODY)
        + "_"
        + COMMENT_MELODY
        + ".png"
    )

    # Optional: Show the plot
    plt.show()


def process_data(batch):
    """
    Process the data in the batch and convert it into tensors for training.

    Args:
    ----------
        batch (list): A list of sequences, where each sequence contains input and label data.

    Returns:
    ----------
        tuple: A tuple containing the processed inputs, targets, accumulated time tensor, and current chord time left tensor.
            - inputs (tuple): A tuple of tensors representing the input data, including pitches, durations, current chords, and next chords.
            - targets (tuple): A tuple of tensors representing the target data, including ground truth pitches and durations.
            - accumulated_time_tensor (torch.Tensor): A tensor representing the accumulated times for each sequence in the batch.
            - current_chord_time_left_tensor (torch.Tensor): A tensor representing the time left for the current chord in each sequence.
    """

    # batch - input/label - Sequence - note - pitch/duration/chord
    # Initialize lists to store sequence data
    pitches, durations, current_chords, next_chords = [], [], [], []
    current_chord_time_lefts, accumulated_times = [], []
    ground_truth_pitches, ground_truth_durations = [], []

    # Iterate through each sequence of inputs in the batch
    for idx, sequence in enumerate(batch):
        pitches.append([])
        durations.append([])
        current_chords.append([])
        next_chords.append([])
        current_chord_time_lefts.append([])
        accumulated_times.append([])
        for note in sequence[0]:
            # Convert each item to a tensor before appending
            pitches[idx].append(torch.tensor(note[0]))
            durations[idx].append(torch.tensor(note[1]))
            current_chords[idx].append(torch.tensor(note[2]))
            next_chords[idx].append(torch.tensor(note[3]))
            current_chord_time_lefts[idx].append(torch.tensor(note[4]))
            accumulated_times[idx].append(torch.tensor(note[5]))
        pitches[idx] = torch.stack(pitches[idx])
        durations[idx] = torch.stack(durations[idx])
        current_chords[idx] = torch.stack(current_chords[idx])
        next_chords[idx] = torch.stack(next_chords[idx])
        current_chord_time_lefts[idx] = torch.stack(current_chord_time_lefts[idx])
        accumulated_times[idx] = torch.stack(accumulated_times[idx])

    # Iterate through each sequence of labels in the batch
    for idx, sequence in enumerate(batch):
        ground_truth_pitches.append([])
        ground_truth_durations.append([])
        for note in sequence[1]:
            # Convert each item to a tensor before appending
            ground_truth_pitches[idx].append(torch.tensor(note[0]))
            ground_truth_durations[idx].append(torch.tensor(note[1]))
        ground_truth_pitches[idx] = torch.stack(ground_truth_pitches[idx])
        ground_truth_durations[idx] = torch.stack(ground_truth_durations[idx])

    # Convert lists of tensors to a single tensor for each
    pitches_tensor = torch.stack(pitches)
    pitches_tensor = torch.stack(pitches)
    durations_tensor = torch.stack(durations)
    current_chord_tensor = torch.stack(current_chords)
    next_chord_tensor = torch.stack(next_chords)
    current_chord_time_left_tensor = torch.stack(current_chord_time_lefts)
    accumulated_time_tensor = torch.stack(accumulated_times)

    # Stack ground truth pitches and durations
    ground_truth_pitches_tensor = torch.stack(ground_truth_pitches)
    ground_truth_durations_tensor = torch.stack(ground_truth_durations)

    # Group the inputs and targets
    inputs = (pitches_tensor, durations_tensor, current_chord_tensor, next_chord_tensor)
    targets = (ground_truth_pitches_tensor, ground_truth_durations_tensor)

    return inputs, targets, accumulated_time_tensor, current_chord_time_left_tensor
