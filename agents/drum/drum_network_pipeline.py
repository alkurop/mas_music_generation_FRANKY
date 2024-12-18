import sys
import math
import os
import time
import json
import itertools

import torch
import torch.optim as optim
import torch.nn as nn

from .drum_network import Drum_Network

from data_processing import Drum_Dataset

from config import WORK_DIR, MODEL_PATH_DRUM, DEVICE, VERSION
from .utils import create_exp_dir, create_dir_if_not_exists

sys.path.append("utils")


def drum_network_pipeline(conf: dict, drum_dataset: Drum_Dataset):
    """
    Run model pipeline from setup specified in <conf>

    Params
    ----------
    conf: dict
        config from conf/train_conf.yaml
    drum_dataset: Drum_Dataset
        Drum_Dataset object
    """
    model_conf = conf["model"]
    data_conf = conf["data"]

    if model_conf["d_embed"] < 0:
        model_conf["d_embed"] = model_conf["d_model"]

    assert model_conf["ext_len"] >= 0, "extended context length must be non-negative"
    assert model_conf["train_batch_size"] % model_conf["batch_chunk"] == 0

    model_conf["work_dir"] = "{}-{}".format(
        model_conf["work_dir"], data_conf["dataset"]
    )
    model_conf["work_dir"] = os.path.join(
        model_conf["work_dir"], time.strftime("%Y%m%d-%H%M%S")
    )
    # logging = create_exp_dir(model_conf['work_dir'],
    #   scripts_to_save=['train.py', 'mem_transformer.py'], debug=model_conf['debug'])
    logging = create_exp_dir(WORK_DIR, scripts_to_save=None, debug=model_conf["debug"])
    loss_list = []
    val_loss_list = []

    # Set the random seed manually for reproducibility.
    # np.random.seed(model_conf['seed'])
    # torch.manual_seed(model_conf['seed'])
    if torch.cuda.is_available():
        if not model_conf["cuda"]:
            print(
                "WARNING: You have a CUDA device, so you should probably run with --cuda"
            )
        else:
            pass
            # torch.cuda.manual_seed_all(model_conf['seed'])

    # Validate `--fp16` option
    if model_conf["fp16"]:
        if not model_conf["cuda"]:
            print("WARNING: --fp16 requires --cuda, ignoring --fp16 option")
            model_conf["fp16"] = False
        else:
            try:
                from apex.fp16_utils import FP16_Optimizer
            except:
                print("WARNING: apex not installed, ignoring --fp16 option")
                model_conf["fp16"] = False

    ###############################################################################
    # Load data
    ###############################################################################

    ntokens = drum_dataset.vocab_size
    model_conf["n_token"] = ntokens

    cutoffs, tie_projs = [], [False]

    eval_batch_size = 10
    tr_iter = drum_dataset.get_iterator(
        "train",
        model_conf["train_batch_size"],
        model_conf["tgt_len"],
        device=DEVICE,
        ext_len=model_conf["ext_len"],
    )
    va_iter = drum_dataset.get_iterator(
        "valid",
        eval_batch_size,
        model_conf["tgt_len"],
        device=DEVICE,
        ext_len=model_conf["ext_len"],
    )
    te_iter = drum_dataset.get_iterator(
        "test",
        eval_batch_size,
        model_conf["tgt_len"],
        device=DEVICE,
        ext_len=model_conf["ext_len"],
    )

    ###############################################################################
    # Build the model
    ###############################################################################
    def init_weight(weight):
        if model_conf["init"] == "uniform":
            nn.init.uniform_(
                weight, -model_conf["init_range"], model_conf["init_range"]
            )
        elif model_conf["init"] == "normal":
            nn.init.normal_(weight, 0.0, model_conf["init_std"])

    def init_bias(bias):
        nn.init.constant_(bias, 0.0)

    def weights_init(m):
        classname = m.__class__.__name__
        if classname.find("Linear") != -1:
            if hasattr(m, "weight") and m.weight is not None:
                init_weight(m.weight)
            if hasattr(m, "bias") and m.bias is not None:
                init_bias(m.bias)
        elif classname.find("AdaptiveEmbedding") != -1:
            if hasattr(m, "emb_projs"):
                for i in range(len(m.emb_projs)):
                    if m.emb_projs[i] is not None:
                        nn.init.normal_(
                            m.emb_projs[i], 0.0, model_conf["proj_init_std"]
                        )
        elif classname.find("Embedding") != -1:
            if hasattr(m, "weight"):
                init_weight(m.weight)
        elif classname.find("ProjectedAdaptiveLogSoftmax") != -1:
            if hasattr(m, "cluster_weight") and m.cluster_weight is not None:
                init_weight(m.cluster_weight)
            if hasattr(m, "cluster_bias") and m.cluster_bias is not None:
                init_bias(m.cluster_bias)
            if hasattr(m, "out_projs"):
                for i in range(len(m.out_projs)):
                    if m.out_projs[i] is not None:
                        nn.init.normal_(
                            m.out_projs[i], 0.0, model_conf["proj_init_std"]
                        )
        elif classname.find("LayerNorm") != -1:
            if hasattr(m, "weight"):
                nn.init.normal_(m.weight, 1.0, model_conf["init_std"])
            if hasattr(m, "bias") and m.bias is not None:
                init_bias(m.bias)
        elif classname.find("TransformerLM") != -1:
            if hasattr(m, "r_emb"):
                init_weight(m.r_emb)
            if hasattr(m, "r_w_bias"):
                init_weight(m.r_w_bias)
            if hasattr(m, "r_r_bias"):
                init_weight(m.r_r_bias)
            if hasattr(m, "r_bias"):
                init_bias(m.r_bias)

    def update_dropout(m):
        classname = m.__class__.__name__
        if classname.find("Dropout") != -1:
            if hasattr(m, "p"):
                m.p = model_conf["dropout"]

    def update_dropatt(m):
        if hasattr(m, "dropatt"):
            m.dropatt.p = model_conf["dropatt"]

    if model_conf["restart"]:
        with open(os.path.join(model_conf["restart_dir"], "model.pt"), "rb") as f:
            model = torch.load(f)
        if not model_conf["fp16"]:
            model = model.float()
        model.apply(update_dropout)
        model.apply(update_dropatt)
    else:
        model = Drum_Network(
            ntokens,
            model_conf["n_layer"],
            model_conf["n_head"],
            model_conf["d_model"],
            model_conf["d_head"],
            model_conf["d_inner"],
            model_conf["dropout"],
            model_conf["dropatt"],
            tie_weight=model_conf["not_tied"],
            d_embed=model_conf["d_embed"],
            div_val=model_conf["div_val"],
            tie_projs=tie_projs,
            pre_lnorm=model_conf["pre_lnorm"],
            tgt_len=model_conf["tgt_len"],
            ext_len=model_conf["ext_len"],
            mem_len=model_conf["mem_len"],
            cutoffs=cutoffs,
            same_length=model_conf["same_length"],
            attn_type=model_conf["attn_type"],
            clamp_len=model_conf["clamp_len"],
            sample_softmax=model_conf["sample_softmax"],
        )
        model.apply(weights_init)
        model.word_emb.apply(
            weights_init
        )  # ensure embedding init is not overridden by out_layer in case of weight sharing
    model_conf["n_all_param"] = sum([p.nelement() for p in model.parameters()])
    model_conf["n_nonemb_param"] = sum(
        [p.nelement() for p in model.layers.parameters()]
    )

    if model_conf["fp16"]:
        model = model.half()

    if model_conf["multi_gpu"]:
        model = model.to(DEVICE)
        para_model = nn.DataParallel(model, dim=1).to(DEVICE)
    else:
        para_model = model.to(DEVICE)

    #### optimizer
    if model_conf["optim"].lower() == "sgd":
        if model_conf["sample_softmax"] > 0:
            dense_params, sparse_params = [], []
            for param in model.parameters():
                if param.size() == model.word_emb.weight.size():
                    sparse_params.append(param)
                else:
                    dense_params.append(param)
            optimizer_sparse = optim.SGD(
                sparse_params, lr=model_conf["learning_rate"] * 2
            )
            optimizer = optim.SGD(
                dense_params, lr=model_conf["learning_rate"], momentum=model_conf["mom"]
            )
        else:
            optimizer = optim.SGD(
                model.parameters(),
                lr=model_conf["learning_rate"],
                momentum=model_conf["mom"],
            )
    elif model_conf["optim"].lower() == "adam":
        if model_conf["sample_softmax"] > 0:
            dense_params, sparse_params = [], []
            for param in model.parameters():
                if param.size() == model.word_emb.weight.size():
                    sparse_params.append(param)
                else:
                    dense_params.append(param)
            optimizer_sparse = optim.SparseAdam(
                sparse_params, lr=model_conf["learning_rate"]
            )
            optimizer = optim.Adam(dense_params, lr=model_conf["learning_rate"])
        else:
            optimizer = optim.Adam(model.parameters(), lr=model_conf["learning_rate"])
    elif model_conf["optim"].lower() == "adagrad":
        optimizer = optim.Adagrad(model.parameters(), lr=model_conf["learning_rate"])

    #### scheduler
    if model_conf["scheduler"] == "cosine":
        # here we do not set eta_min to lr_min to be backward compatible
        # because in previous versions eta_min is default to 0
        # rather than the default value of lr_min 1e-6
        scheduler = optim.lr_scheduler.CosineAnnealingLR(
            optimizer, model_conf["max_step"], eta_min=model_conf["eta_min"]
        )  # should use eta_min arg
        if model_conf["sample_softmax"] > 0:
            scheduler_sparse = optim.lr_scheduler.CosineAnnealingLR(
                optimizer_sparse, model_conf["max_step"], eta_min=model_conf["eta_min"]
            )  # should use eta_min arg
    elif model_conf["scheduler"] == "inv_sqrt":
        # originally used for Transformer (in Attention is all you need)
        def lr_lambda(step):
            # return a multiplier instead of a learning rate
            if step == 0 and model_conf["warmup_steps"] == 0:
                return 1.0
            else:
                return (
                    1.0 / (step**0.5)
                    if step > model_conf["warmup_steps"]
                    else step / (model_conf["warmup_steps"] ** 1.5)
                )

        scheduler = optim.lr_scheduler.LambdaLR(optimizer, lr_lambda=lr_lambda)
    elif model_conf["scheduler"] == "dev_perf":
        scheduler = optim.lr_scheduler.ReduceLROnPlateau(
            optimizer,
            factor=model_conf["decay_rate"],
            patience=model_conf["patience"],
            min_lr=model_conf["lr_min"],
        )
        if model_conf["sample_softmax"] > 0:
            scheduler_sparse = optim.lr_scheduler.ReduceLROnPlateau(
                optimizer_sparse,
                factor=model_conf["decay_rate"],
                patience=model_conf["patience"],
                min_lr=model_conf["lr_min"],
            )
    elif model_conf["scheduler"] == "constant":
        pass

    if model_conf["cuda"] and model_conf["fp16"]:
        # If model_conf['dynamic_loss_scale'] is False, static_loss_scale will be used.
        # If model_conf['dynamic_loss_scale'] is True, it will take precedence over static_loss_scale.
        optimizer = FP16_Optimizer(
            optimizer,
            static_loss_scale=model_conf["static_loss_scale"],
            dynamic_loss_scale=model_conf["dynamic_loss_scale"],
            dynamic_loss_args={"init_scale": 2**16},
        )

    if model_conf["restart"]:
        if os.path.exists(os.path.join(model_conf["restart_dir"], "optimizer.pt")):
            with open(
                os.path.join(model_conf["restart_dir"], "optimizer.pt"), "rb"
            ) as f:
                opt_state_dict = torch.load(f)
                optimizer.load_state_dict(opt_state_dict)
        else:
            print("Optimizer was not saved. Start from scratch.")

    logging("=" * 100)
    for k, v in model_conf.items():
        logging("    - {} : {}".format(k, v))
    logging("=" * 100)
    logging("#params = {}".format(model_conf["n_all_param"]))
    logging("#non emb params = {}".format(model_conf["n_nonemb_param"]))

    ###############################################################################
    # Training code
    ###############################################################################

    def evaluate(eval_iter):
        # Turn on evaluation mode which disables dropout.
        model.eval()

        # If the model does not use memory at all, make the ext_len longer.
        # Otherwise, make the mem_len longer and keep the ext_len the same.
        if model_conf["mem_len"] == 0:
            model.reset_length(
                model_conf["eval_tgt_len"],
                model_conf["ext_len"]
                + model_conf["tgt_len"]
                - model_conf["eval_tgt_len"],
                model_conf["mem_len"],
            )
        else:
            model.reset_length(
                model_conf["eval_tgt_len"],
                model_conf["ext_len"],
                model_conf["mem_len"]
                + model_conf["tgt_len"]
                - model_conf["eval_tgt_len"],
            )

        # Evaluation
        total_len, total_loss = 0, 0.0
        with torch.no_grad():
            mems = tuple()
            for i, (data, target, seq_len) in enumerate(eval_iter):
                if (
                    model_conf["max_eval_steps"] > 0
                    and i >= model_conf["max_eval_steps"]
                ):
                    break
                ret = model(data, target, *mems)
                loss, mems = ret[0], ret[1:]
                loss = loss.mean()
                total_loss += seq_len * loss.float().item()
                total_len += seq_len


        # Switch back to the training mode
        model.reset_length(
            model_conf["tgt_len"], model_conf["ext_len"], model_conf["mem_len"]
        )
        model.train()
        
        print("total_loss", total_loss)
        print("total_len", total_len)

        return total_loss / total_len

    def train():
        # Turn on training mode which enables dropout.
        nonlocal train_step, train_loss, best_val_loss, eval_start_time, log_start_time
        model.train()
        if model_conf["batch_chunk"] > 1:
            mems = [tuple() for _ in range(model_conf["batch_chunk"])]
        else:
            mems = tuple()
        train_iter = tr_iter.get_varlen_iter() if model_conf["varlen"] else tr_iter
        for batch, (data, target, seq_len) in enumerate(train_iter):
            model.zero_grad()
            if model_conf["batch_chunk"] > 1:
                data_chunks = torch.chunk(data, model_conf["batch_chunk"], 1)
                target_chunks = torch.chunk(target, model_conf["batch_chunk"], 1)
                for i in range(model_conf["batch_chunk"]):
                    data_i = data_chunks[i].contiguous()
                    target_i = target_chunks[i].contiguous()
                    ret = para_model(data_i, target_i, *mems[i])
                    loss, mems[i] = ret[0], ret[1:]
                    loss = loss.float().mean().type_as(loss) / model_conf["batch_chunk"]
                    if model_conf["fp16"]:
                        optimizer.backward(loss)
                    else:
                        loss.backward()
                    train_loss += loss.float().item()
            else:
                ret = para_model(data, target, *mems)
                loss, mems = ret[0], ret[1:]
                loss = loss.float().mean().type_as(loss)
                if model_conf["fp16"]:
                    optimizer.backward(loss)
                else:
                    loss.backward()
                train_loss += loss.float().item()

            if model_conf["fp16"]:
                optimizer.clip_master_grads(model_conf["clip"])
            else:
                torch.nn.utils.clip_grad_norm_(model.parameters(), model_conf["clip"])

            optimizer.step()
            if model_conf["sample_softmax"] > 0:
                optimizer_sparse.step()

            # step-wise learning rate annealing
            train_step += 1
            if model_conf["scheduler"] in ["cosine", "constant", "dev_perf"]:
                # linear warmup stage
                if train_step < model_conf["warmup_steps"]:
                    curr_lr = (
                        model_conf["learning_rate"]
                        * train_step
                        / model_conf["warmup_steps"]
                    )
                    optimizer.param_groups[0]["lr"] = curr_lr
                    if model_conf["sample_softmax"] > 0:
                        optimizer_sparse.param_groups[0]["learning_rate"] = curr_lr * 2
                else:
                    if model_conf["scheduler"] == "cosine":
                        scheduler.step()
                        if model_conf["sample_softmax"] > 0:
                            scheduler_sparse.step(train_step)
            elif model_conf["scheduler"] == "inv_sqrt":
                scheduler.step()

            if train_step % model_conf["log_interval"] == 0:
                cur_loss = train_loss / model_conf["log_interval"]
                elapsed = time.time() - log_start_time
                log_str = (
                    "| epoch {:3d} step {:>8d} | {:>6d} batches | lr {:.3g} "
                    "| ms/batch {:5.2f} | loss {:5.2f}".format(
                        epoch,
                        train_step,
                        batch + 1,
                        optimizer.param_groups[0]["lr"],
                        elapsed * 1000 / model_conf["log_interval"],
                        cur_loss,
                    )
                )
                log_str += " | ppl {:9.3f}".format(math.exp(cur_loss))
                logging(log_str)
                train_loss = 0
                log_start_time = time.time()
                loss_list.append(cur_loss)

            if train_step == 1 or train_step % model_conf["eval_interval"] == 0:
                val_loss = evaluate(va_iter)
                logging("-" * 100)
                log_str = (
                    "| Eval {:3d} at step {:>8d} | time: {:5.2f}s "
                    "| valid loss {:5.2f}".format(
                        train_step // model_conf["eval_interval"],
                        train_step,
                        (time.time() - eval_start_time),
                        val_loss,
                    )
                )
                val_loss_list.append(val_loss)
                log_str += " | valid ppl {:9.3f}".format(math.exp(val_loss))
                logging(log_str)
                logging("-" * 100)
                # Save the model if the validation loss is the best we've seen so far.
                if not best_val_loss or val_loss < best_val_loss:
                    create_dir_if_not_exists(
                        os.path.join(WORK_DIR, VERSION, f"train_step_{train_step}", "")
                    )
                    if not model_conf["debug"]:
                        with open(
                            os.path.join(
                                WORK_DIR,
                                f"train_step_{train_step}",
                                "model.pt",
                            ),
                            "wb",
                        ) as f:
                            torch.save(model, f)
                        with open(
                            os.path.join(
                                WORK_DIR,
                                f"train_step_{train_step}",
                                "optimizer.pt",
                            ),
                            "wb",
                        ) as f:
                            torch.save(optimizer.state_dict(), f)
                    best_val_loss = val_loss

                # dev-performance based learning rate annealing
                if model_conf["scheduler"] == "dev_perf":
                    scheduler.step(val_loss)
                    if model_conf["sample_softmax"] > 0:
                        scheduler_sparse.step(val_loss)

                eval_start_time = time.time()

            if train_step == model_conf["max_step"]:
                torch.save(model, MODEL_PATH_DRUM)
                break

    # Loop over epochs.
    train_step = 0
    train_loss = 0
    best_val_loss = None

    log_start_time = time.time()
    eval_start_time = time.time()

    # At any point you can hit Ctrl + C to break out of training early.
    try:
        for epoch in itertools.count(start=1):
            train()
            if train_step == model_conf["max_step"]:
                logging("-" * 100)
                logging("End of training")
                save_to_json(loss_list, val_loss_list)
                break
    except KeyboardInterrupt:
        logging("-" * 100)
        logging("Exiting from training early")

    create_dir_if_not_exists(WORK_DIR)
    # Load the newest model.
    model = torch.load(WORK_DIR + "/drum_model_small.pt")
    # para_model = model.to(device)

    # Run on test data.
    test_loss = evaluate(te_iter)
    logging("=" * 100)
    logging(
        "| End of training | test loss {:5.2f} | test ppl {:9.3f}".format(
            test_loss, math.exp(test_loss)
        )
    )
    logging("=" * 100)

    return model

def save_to_json(loss_list, val_loss_list):
    
    with open(
        "results/data/drum/training_data" + str(VERSION) +".json", "w"
    ) as file:
        json.dump(loss_list, file)
        json.dump(val_loss_list, file)