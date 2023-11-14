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
    DEVICE,
    PITCH_SIZE_MELODY,
    DURATION_SIZE_MELODY,
    ALPHA1,
    ALPHA2,
    DATASET_SIZE_MELODY,
)


def train_melody(
    model: Melody_Network,
    dataset: Melody_Dataset,
) -> None:
    """
    Trains the MelodyGenerator model on the provided dataset.

    Args:
        model (MelodyGenerator): The neural network model to train.
        dataset (Dataset): The dataset to train the model on.


    Returns:
        list: A list of average epoch losses for each epoch.
    """

    dataloader = DataLoader(
        dataset, batch_size=BATCH_SIZE_MELODY, shuffle=False, collate_fn=process_data
    )
    # Define loss function and optimizer
    criterion = nn.CrossEntropyLoss()
    optimizer = optim.Adam(model.parameters(), lr=LEARNING_RATE_MELODY)

    all_loss = []

    # Training loop
    for epoch in range(NUM_EPOCHS_MELODY):
        for idx, batch in enumerate(dataloader):
            (
                pitches,
                durations,
                current_chord,
                next_chord,
            ) = batch[0]
            gt_pitches, gt_durations = batch[1]
            accumulated_time = batch[2]
            time_left_on_chord = batch[3]

            # Zero the parameter gradients
            optimizer.zero_grad()

            x = torch.cat(
                (
                    pitches,
                    durations,
                    current_chord,
                    next_chord,
                ),
                dim=1,
            )

            pitch_logits, duration_logits = model(
                x, accumulated_time, time_left_on_chord
            )

            # Calculate loss
            pitch_loss = criterion(pitch_logits, get_gt(gt_pitches))
            duration_loss = criterion(duration_logits, get_gt(gt_durations))

            loss = pitch_loss * ALPHA1 + duration_loss * ALPHA2

            all_loss.append(loss.item())

            # Backward pass and optimize
            loss.backward()
            optimizer.step()
            print(f"batch {idx}, Loss: {loss.item()}")
        print(f"Epoch {epoch+1}, Loss: {loss.item()}")

    # Save the model
    torch.save(model, MODEL_PATH_MELODY)
    plot_loss(all_loss)

    with open(
        "results/data/" + DATASET_SIZE_MELODY + str(NUM_EPOCHS_MELODY) + ".json", "w"
    ) as file:
        json.dump(all_loss, file)
    plt.show()


def get_gt(gt):
    if len(gt.size()) > 1 and gt.size(1) > 1:  # Check for one-hot encoding
        return torch.argmax(gt, dim=1)


def plot_loss(loss_values: list[float]) -> None:
    """
    Plots the training loss over batches.

    Parameters
    ----------
    loss_values : List[float]
        A list of loss values to be plotted.

    Returns
    -------
    None
    """

    plt.figure()
    plt.plot(loss_values)
    plt.title("Training Loss melody")
    plt.xlabel("Batch")
    plt.ylabel("Loss")
    plt.savefig("figures/melody_training_loss.png")


def process_data(batch):
    pitches = [item[0][0] for item in batch]
    durations = [item[0][1] for item in batch]
    current_chord = [item[0][2] for item in batch]
    next_chord = [item[0][3] for item in batch]
    current_chord_time_left = [item[0][4] for item in batch]
    accumulated_time = [item[0][5] for item in batch]

    ground_truth_pitches = [item[1][0] for item in batch]
    ground_truth_durations = [item[1][1] for item in batch]

    # Convert lists of tensors to a single tensor for each

    pitches_tensor = torch.stack(pitches)
    durations_tensor = torch.stack(durations)
    current_chord_tensor = torch.stack(current_chord)
    next_chord_tensor = torch.stack(next_chord)
    current_chord_time_left_tensor = torch.stack(current_chord_time_left)
    accumulated_time_tensor = torch.stack(accumulated_time)

    # Stack ground truth pitches and durations
    ground_truth_pitches_tensor = torch.stack(ground_truth_pitches)
    ground_truth_durations_tensor = torch.stack(ground_truth_durations)

    inputs = (
        pitches_tensor,
        durations_tensor,
        current_chord_tensor,
        next_chord_tensor,
    )
    targets = (
        ground_truth_pitches_tensor,
        ground_truth_durations_tensor,
    )

    return inputs, targets, accumulated_time_tensor, current_chord_time_left_tensor
