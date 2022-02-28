#
# Copyright (c) 2021 The Board of Trustees of the Leland Stanford Junior University
# All rights reserved.
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions are met:
#
# * Redistributions of source code must retain the above copyright notice, this
#   list of conditions and the following disclaimer.
#
# * Redistributions in binary form must reproduce the above copyright notice,
#   this list of conditions and the following disclaimer in the documentation
#   and/or other materials provided with the distribution.
#
# * Neither the name of the copyright holder nor the names of its
#   contributors may be used to endorse or promote products derived from
#   this software without specific prior written permission.
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
# AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
# IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
# DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE
# FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL
# DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR
# SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER
# CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY,
# OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
# OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.

import logging

import torch
from transformers import AutoConfig, AutoModelForTokenClassification

from ..data_utils.numericalizer import TransformerNumericalizer
from ..data_utils.progbar import progress_bar
from ..models.base import GenieModel
from ..util import GenerationOutput, adjust_language_code

logger = logging.getLogger(__name__)


class TransformerForTokenClassification(GenieModel):
    def __init__(self, config=None, *inputs, args, tasks, vocab_sets, save_directory=None, **kwargs):
        self._init_common(args, tasks, **kwargs)
        if save_directory is not None:
            self.model = AutoModelForTokenClassification.from_config(self.config)
        else:
            self.model = AutoModelForTokenClassification.from_pretrained(
                self.args.pretrained_model, cache_dir=self.args.embeddings, config=self.config
            )

        self.numericalizer = TransformerNumericalizer(
            self.args.pretrained_model,
            args,
            max_generative_vocab=None,
            save_dir=save_directory,
            config=self.config,
            src_lang=self.src_lang,
            tgt_lang=self.tgt_lang,
            vocab_sets=vocab_sets,
            tasks=tasks,
        )

        self.model.resize_token_embeddings(self.numericalizer.num_tokens)
        self.numericalizer.answer_pad_id = -100

    def _init_common(self, args, tasks, **kwargs):
        self.args = args
        num_labels = 0
        if args.num_labels is not None:
            num_labels = args.num_labels
        else:
            for task in tasks:
                # if having multiple tasks choose max num_labels
                if hasattr(task, 'num_labels'):
                    num_labels = max(num_labels, task.num_labels)

        config = AutoConfig.from_pretrained(
            args.pretrained_model, cache_dir=args.embeddings, num_labels=num_labels, finetuning_task='ned'
        )
        super().__init__(config)

        if hasattr(config, 'd_model'):
            args.dimension = config.d_model
        else:
            args.dimension = config.hidden_size

        self.src_lang, self.tgt_lang = adjust_language_code(
            config, args.pretrained_model, kwargs.get('src_lang', 'en'), kwargs.get('tgt_lang', 'en')
        )

    def add_new_vocab_from_data(self, tasks, resize_decoder=False):
        super().add_new_vocab_from_data(tasks, resize_decoder)
        self.model.resize_token_embeddings(self.numericalizer.num_tokens)

    def forward(self, *input, **kwargs):
        if self.training:
            batch = input[0]
            outputs = self.model(
                batch.context.value,
                labels=batch.answer.value,
                attention_mask=(batch.context.value != self.numericalizer.pad_id),
            )
            return outputs
        else:
            return self.model(**kwargs)

    def validate(self, data_iterator, task, original_order=None, disable_progbar=True, **kwargs):
        total_loss = 0.0
        all_example_ids = []
        all_answers = []
        all_contexts = []
        all_predictions = []

        for batch in progress_bar(data_iterator, desc='Generating', disable=disable_progbar):
            batch_example_ids = batch.example_id

            batch_context = self.numericalizer.reverse(batch.context.value.data, 'context')

            all_example_ids += batch_example_ids

            # pass labels to get loss
            output = self.forward(
                input_ids=batch.context.value,
                attention_mask=(batch.context.value != self.numericalizer.pad_id),
                labels=batch.answer.value,
            )

            labels = batch.answer.value.tolist()

            logits = output.logits
            predictions = torch.argmax(logits, dim=-1).tolist()

            # logits for sequence classification is 2 dimensional
            if logits.dim() == 2:
                predictions = [[p] for p in predictions]

            # Remove ignored index (special tokens)
            processed_preds = []
            processed_labels = []
            for pred, label in zip(predictions, labels):
                preds_list = []
                labels_list = []
                for p_, l_ in zip(pred, label):
                    if l_ == self.numericalizer.answer_pad_id:
                        continue
                    preds_list.append(task.id2label[p_])
                    labels_list.append(task.id2label[l_])

                processed_preds.append([" ".join(preds_list)])
                processed_labels.append(" ".join(labels_list))

            all_contexts += batch_context
            all_answers += processed_labels
            all_predictions += processed_preds

            total_loss += output.loss

        total_loss /= len(all_example_ids)

        if original_order is not None:
            # sort back to the original order
            original_order, all_example_ids, all_predictions, all_answers, all_contexts = [
                list(a)
                for a in tuple(
                    zip(*sorted(list(zip(original_order, all_example_ids, all_predictions, all_answers, all_contexts))))
                )
            ]

        output = GenerationOutput(
            loss=total_loss,
            example_ids=all_example_ids,
            contexts=all_contexts,
            answers=all_answers,
            predictions=all_predictions,
        )

        return output
