python ../finetune.py \
--task 'MRPC' \
--train_batch_size 32 \
--train_type 'ft' \
--model_type 'Original' \
--student_hidden_layers 12 \
--saving_criterion_acc 0.0 \
--saving_criterion_loss 1.0 \
--layer_initialization '1,2,3,4,5,6' \
--output_dir 'teacher_12layer' \

python ../save_teacher_outputs.py \

python ../finetune_NL.py \
--task 'MRPC' \
--train_type 'pkd' \
--model_type 'NL' \
--NL_mode 0 \
--student_hidden_layer 6 \
--saving_criterion_acc 0.0 \
--saving_criterion_loss 1.0 \
--teacher_prediction '/home/ikhyuncho23/data/outputs/KD/MRPC/MRPC_Originalbert_base_patient_kd_teacher_12layer_result_summary.pkl' \
--load_model_dir 'teacher_12layer/BERT.encoder_loss.pkl' \
--layer_initialization '1,2,3,4,5,6,7,8,9,10,11,12,2,4,6,8,10,12' \
--output_dir 'NL_run_1' \

python ../finetune.py \
--task 'MRPC' \
--train_type 'pkd' \
--model_type 'Original' \
--student_hidden_layer 6 \
--saving_criterion_acc 1.0 \
--saving_criterion_loss 0.0 \
--teacher_prediction '/home/ikhyuncho23/data/outputs/KD/MRPC/MRPC_Originalbert_base_patient_kd_teacher_12layer_result_summary.pkl' \
--load_model_dir 'NL_run_1/BERT.encoder_loss_a;;.pkl' \
--layer_initialization '2,4,6,8,10,12' \
--output_dir 'NL_result_1'