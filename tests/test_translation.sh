#!/usr/bin/env bash

. ./tests/lib.sh

i=0
# translation tests (with `genienlp train`)
mkdir -p $workdir/translation/almond
cp -r $SRCDIR/dataset/translation/en-de $workdir/translation

for model in "Helsinki-NLP/opus-mt-en-de" "sshleifer/tiny-mbart" ; do

    if [[ $model == Helsinki-NLP* ]] ; then
      base_model="marian"
      expected_result='{"casedbleu": 95.12283373900253}'
    elif [[ $model == *mbart* ]] ; then
      base_model="mbart"
      expected_result='{"casedbleu": 4.200510937048206}'
    fi

    mv $workdir/translation/en-de/dev_"$base_model"_aligned.tsv $workdir/translation/almond/train.tsv
    cp $workdir/translation/almond/train.tsv $workdir/translation/almond/eval.tsv

    # train
    genienlp train  --train_tasks almond_translate --do_alignment --train_languages en --train_tgt_languages de --eval_languages en --eval_tgt_languages de --model TransformerSeq2Seq --pretrained_model $model --train_batch_tokens 100 --val_batch_size 100 --train_iterations 6 --preserve_case --save_every 2 --log_every 2 --val_every 2 --save $workdir/model_$i --data $workdir/translation/ --exist_ok  --embeddings $EMBEDDING_DIR --no_commit

    # greedy prediction
    genienlp predict --tasks almond_translate --evaluate valid --pred_languages en --pred_tgt_languages de --path $workdir/model_$i --overwrite --eval_dir $workdir/model_$i/eval_results/ --data $workdir/translation/ --embeddings $EMBEDDING_DIR

    # check if result file exists and matches expected_result
    echo $expected_result | diff -u - $workdir/model_$i/eval_results/valid/almond_translate.results.json

    rm -rf $workdir/generated_"$base_model"_aligned.tsv

    i=$((i+1))
done

# translation tests
mkdir -p $workdir/translation
cp -r $SRCDIR/dataset/translation/en-de $workdir/translation

for model in "t5-small" "Helsinki-NLP/opus-mt-en-de" ; do

  if [[ $model == *t5* ]] ; then
    base_model="t5"
  elif [[ $model == Helsinki-NLP* ]] ; then
    base_model="marian"
  fi

  # use a pre-trained model
  genienlp run-paraphrase --model_name_or_path $model --length 15 --temperature 0 --repetition_penalty 1.0 --num_samples 1 --batch_size 3 --input_file $workdir/translation/en-de/dev_"$base_model"_aligned.tsv --input_column 1 --gold_column 2 --output_file $workdir/generated_"$base_model"_aligned.tsv  --skip_heuristics --att_pooling mean --task translate --src_lang en --tgt_lang de --replace_qp --output_attentions

  if [ $i == 2 ] ; then
    # check if predictions matches expected_results
    diff -u $SRCDIR/expected_results/translation/t5_small_en_de.tsv $workdir/generated_"$base_model"_aligned.tsv
  elif [ $i == 3 ] ; then
    # check if predictions matches expected_results
    diff -u $SRCDIR/expected_results/translation/marian_en_de.tsv $workdir/generated_"$base_model"_aligned.tsv
  fi

  rm -rf $workdir/generated_"$base_model"_aligned.tsv

  i=$((i+1))

done
