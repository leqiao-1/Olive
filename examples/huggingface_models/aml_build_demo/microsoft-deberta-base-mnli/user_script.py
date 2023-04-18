# -------------------------------------------------------------------------
# Copyright (c) Microsoft Corporation. All rights reserved.
# Licensed under the MIT License.
# --------------------------------------------------------------------------
import torch
from datasets import load_dataset
from onnxruntime.quantization import CalibrationDataReader
from torch.utils.data import DataLoader, Dataset
from torch.utils.data.dataloader import default_collate
from transformers import AutoModelForSequenceClassification, AutoTokenizer

# https://huggingface.co/microsoft/deberta-base-mnli
model_name = "microsoft/deberta-base-mnli"
dataset_name = "glue"
subset = "mnli_matched"
split = "validation"


class CalibrationDataLoader(CalibrationDataReader):
    def __init__(self, dataloader, post_func, num_samplers=100):
        self.dataloader = dataloader
        self.iter = iter(dataloader)
        self.post_func = post_func
        self.counter = 0
        self.num_samplers = num_samplers

    def get_next(self):
        if self.counter >= self.num_samplers:
            return None
        self.counter += 1
        if self.iter is None:
            self.iter = iter(self.dataloader)
        try:
            return self.post_func(next(self.iter))
        except StopIteration:
            return None

    def rewind(self):
        self.iter = None
        self.counter = 0


# -------------------- model -------------------
def load_model(model_path=None):
    model = AutoModelForSequenceClassification.from_pretrained(model_name)
    return model


# -------------------- dataset -------------------
def tokenize_and_align_labels(examples):
    if isinstance(examples["label"], list):
        label = list(map(lambda x: 2 - x, examples["label"]))
    elif isinstance(examples["label"], int):
        label = 2 - examples["label"]
    tokenizer = AutoTokenizer.from_pretrained(model_name)
    tokenized_inputs = tokenizer(
        examples["premise"],
        examples["hypothesis"],
        truncation=True,
        padding=True,
        return_tensors="pt",
    )
    # pre process
    tokenized_inputs["labels"] = torch.LongTensor(label)
    return tokenized_inputs


def create_evaluation_dataset():
    dataset = load_dataset(dataset_name, subset, split=split)
    tokenized_datasets = dataset.map(
        tokenize_and_align_labels,
        batched=True,
        remove_columns=dataset.column_names,
    )
    tokenized_datasets.set_format("torch", columns=tokenized_datasets.column_names)

    class _Dateset(Dataset):
        def __init__(self, dataset):
            self.dataset = dataset

        def __getitem__(self, index):
            return self.dataset[index], self.dataset[index]["labels"]

        def __len__(self):
            return len(self.dataset)

    return _Dateset(tokenized_datasets)


def create_dataloader(data_dir="", batch_size=2):
    def _collate_fn(batch):
        batch = default_collate(batch)
        return batch

    dataset = create_evaluation_dataset()
    return DataLoader(dataset, batch_size=batch_size, collate_fn=_collate_fn)


def create_cali_dataloader():
    def _post_func(sampler):
        return sampler

    dataloader = create_dataloader()
    cali_dataloader = CalibrationDataLoader(create_dataloader(dataloader, _post_func))
    return cali_dataloader


def post_process(output):
    import torch
    import transformers

    if isinstance(output, transformers.modeling_outputs.SequenceClassifierOutput):
        preds = torch.argmax(output.logits, dim=-1)
    else:
        preds = torch.argmax(output, dim=-1)
    return preds
