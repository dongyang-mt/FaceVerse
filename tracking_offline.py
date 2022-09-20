from tkinter import FALSE
import cv2
import os
import numpy as np
import time
import torch
import argparse

from model import get_faceverse
import model.losses as losses

from data_reader import OfflineReader
from util_functions import get_length, ply_from_array_color


def init_optim_with_id(args, faceverse_model):
    rigid_optimizer = torch.optim.Adam([faceverse_model.get_rot_tensor(),
                                        faceverse_model.get_trans_tensor(),
                                        faceverse_model.get_id_tensor(),
                                        faceverse_model.get_exp_tensor()],
                                        lr=args.rf_lr)
    nonrigid_optimizer = torch.optim.Adam(
        [faceverse_model.get_id_tensor(), faceverse_model.get_exp_tensor(),
        faceverse_model.get_gamma_tensor(), faceverse_model.get_tex_tensor(),
        faceverse_model.get_rot_tensor(), faceverse_model.get_trans_tensor()], lr=args.nrf_lr)
    return rigid_optimizer, nonrigid_optimizer


def tracking(args, device):
    faceverse_model, faceverse_dict = get_faceverse(version=args.version, batch_size=1, focal=1315, img_size=args.tar_size, use_simplification=args.use_simplification, device=device)
    lm_weights = losses.get_lm_weights(device)
    offreader = OfflineReader(args.input)
    print(args.input, 'FPS:', offreader.fps)

    os.makedirs(args.res_folder, exist_ok=True)
    fourcc = cv2.VideoWriter_fourcc(*'mp4v')
    basename = os.path.basename(args.input)
    out_video = cv2.VideoWriter(os.path.join(args.res_folder, "faceverse_tracking.mp4"), fourcc, offreader.fps, (args.tar_size * 3, args.tar_size))

    frame_ind = 0
    while True:
        # load data
        face_detected, frame, lms, frame_num = offreader.get_data()
        if not face_detected:
            if frame:
                out_video.release()
                # exit()
                break
            else:
                continue

        # init crop parameters and optimizer
        if frame_ind == 0:
            border = 500
            half_length = int(get_length(lms))
            crop_center = lms[29].copy() + border
            print('First frame:', half_length, crop_center)
            rigid_optimizer, nonrigid_optimizer = init_optim_with_id(args, faceverse_model)
        frame_b = cv2.copyMakeBorder(frame, border, border, border, border, cv2.BORDER_CONSTANT, value=0)
        align = cv2.resize(frame_b[crop_center[1] - half_length:crop_center[1] + half_length, crop_center[0] - half_length:crop_center[0] + half_length],
                            (args.tar_size, args.tar_size), cv2.INTER_AREA)
        resized_lms = (lms - (crop_center - half_length - border)[np.newaxis, :]) / half_length / 2 * args.tar_size
        resized_lms = resized_lms.astype(np.int64)

        lms = torch.from_numpy(resized_lms[np.newaxis, :, :]).type(torch.float32).to(device)
        img_tensor = torch.from_numpy(align[np.newaxis, ...]).type(torch.float32).to(device)

        if frame_ind == 0:
            num_iters_rf = args.first_rf_iters
            num_iters_nrf = args.first_nrf_iters
        else:
            num_iters_rf = args.rest_rf_iters
            num_iters_nrf = args.rest_nrf_iters

        # fitting using only landmarks
        for i in range(num_iters_rf):
            rigid_optimizer.zero_grad()

            pred_dict = faceverse_model(faceverse_model.get_packed_tensors(), render=False, texture=False)
            lm_loss_val = losses.lm_loss(pred_dict['lms_proj'], lms, lm_weights, img_size=args.tar_size)
            exp_reg_loss = losses.get_l2(faceverse_model.get_exp_tensor())
            id_reg_loss = losses.get_l2(faceverse_model.get_id_tensor())
            total_loss = args.lm_loss_w * lm_loss_val + id_reg_loss*args.id_reg_w + exp_reg_loss*args.exp_reg_w

            total_loss.backward()
            rigid_optimizer.step()

            if args.version == 2:
                with torch.no_grad():
                    faceverse_model.exp_tensor[faceverse_model.exp_tensor < 0] *= 0

        # fitting with differentiable rendering
        for i in range(num_iters_nrf):
            nonrigid_optimizer.zero_grad()

            pred_dict = faceverse_model(faceverse_model.get_packed_tensors(), render=True, texture=True)
            rendered_img = pred_dict['rendered_img']
            lms_proj = pred_dict['lms_proj']
            face_texture = pred_dict['face_texture']
            mask = rendered_img[:, :, :, 3].detach()

            lm_loss_val = losses.lm_loss(lms_proj, lms, lm_weights,img_size=args.tar_size)
            photo_loss_val = losses.photo_loss(rendered_img[:, :, :, :3], img_tensor, mask > 0)
            exp_reg_loss = losses.get_l2(faceverse_model.get_exp_tensor())
            id_reg_loss = losses.get_l2(faceverse_model.get_id_tensor())
            tex_reg_loss = losses.get_l2(faceverse_model.get_tex_tensor())
            tex_loss_val = losses.reflectance_loss(face_texture, faceverse_model.get_skinmask())

            loss = lm_loss_val*args.lm_loss_w + id_reg_loss*args.id_reg_w + exp_reg_loss*args.exp_reg_w + \
                    tex_reg_loss*args.tex_reg_w + tex_loss_val*args.tex_w + photo_loss_val*args.rgb_loss_w

            loss.backward()
            nonrigid_optimizer.step()

            if args.version == 2:
                with torch.no_grad():
                    faceverse_model.exp_tensor[faceverse_model.exp_tensor < 0] *= 0

        # save data
        with torch.no_grad():
            pred_dict = faceverse_model(faceverse_model.get_packed_tensors(), render=True, texture=True)
            rendered_img_c = pred_dict['rendered_img']
            rendered_img_c = np.clip(rendered_img_c.cpu().numpy(), 0, 255)
            pred_dict = faceverse_model(faceverse_model.get_packed_tensors(), render=True, texture=False)
            rendered_img_r = pred_dict['rendered_img']
            rendered_img_r = np.clip(rendered_img_r.cpu().numpy(), 0, 255)
        mask_img_c = (rendered_img_c[0, :, :, 3:4] > 0).astype(np.uint8)
        drive_img_c = rendered_img_c[0, :, :, :3].astype(np.uint8) * mask_img_c + align * (1 - mask_img_c)
        mask_img_r = (rendered_img_r[0, :, :, 3:4] > 0).astype(np.uint8)
        drive_img_r = rendered_img_r[0, :, :, :3].astype(np.uint8) * mask_img_r + align * (1 - mask_img_r)
        drive_img = np.concatenate([align, drive_img_c, drive_img_r], axis=1)
        if frame_ind == 0:
            start_t = time.time()
        frame_ind += 1

        out_video.write(drive_img[:, :, ::-1])
        image_folder = os.path.join(args.res_folder, "img")
        os.makedirs(image_folder, exist_ok=True)
        image_path = os.path.join(image_folder, f'{str(frame_ind).zfill(6)}.png')
        cv2.imwrite(image_path, drive_img[:, :, ::-1])
        print(f'Speed:{(time.time() - start_t) / frame_ind:.4f}, {frame_ind:4} / {offreader.num_frames:4}, {total_loss.item():.4f}')

        if args.save_ply:
            vertices = pred_dict['vs'].detach().cpu().squeeze().numpy()
            colors = pred_dict['face_texture'].detach().cpu().squeeze().numpy()
            colors = np.clip(colors, 0, 255).astype(np.uint8)
            ply_folder = os.path.join(args.res_folder, "ply")
            os.makedirs(ply_folder, exist_ok=True)
            output_ply = os.path.join(ply_folder, f'{str(frame_ind).zfill(6)}.ply')
            ply_from_array_color(vertices, colors, faceverse_dict['tri'], output_ply)

        if args.save_coeff:
            coeffs = faceverse_model.get_packed_tensors().detach().clone().cpu().numpy()
            coeffs_folder = os.path.join(args.res_folder, "coeffs")
            os.makedirs(coeffs_folder, exist_ok=True)
            output_coeffs = os.path.join(coeffs_folder, f'{str(frame_ind).zfill(6)}.npy')
            np.save(output_coeffs, coeffs)
            out = {}
            out['id'] = faceverse_model.get_id_tensor()
            out['exp'] = faceverse_model.get_exp_tensor()
            out['tex'] = faceverse_model.get_tex_tensor()
            out['rot'] = faceverse_model.get_rot_tensor()
            out['gamma'] = faceverse_model.get_gamma_tensor()
            out['trans'] = faceverse_model.get_trans_tensor()
            bs_folder = os.path.join(args.res_folder, "bs")
            os.makedirs(bs_folder, exist_ok=True)
            output_bs = os.path.join(bs_folder, f'{str(frame_ind).zfill(6)}.npy')
            with open(os.path.join(output_bs), "wb") as fout:
                import pickle
                pickle.dump(out, fout)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description="FaceVerse online tracker")

    parser.add_argument('--input', type=str, required=False,
                        default="example/videos/test.mp4",
                        help='input video path')
    parser.add_argument('--use_simplification', action='store_true', default=False,
                        help='use the simplified FaceVerse model.')
    parser.add_argument('--res_folder', type=str, required=False,
                        default="example/video_results",
                        help='output directory')
    parser.add_argument('--save_ply', action="store_true", default=True,
                        help='save the output ply or not')
    parser.add_argument('--save_coeff', action="store_true", default=True,
                        help='save the output coeff or not')
    parser.add_argument('--version', type=int, default=2,
                        help='FaceVerse model version.')
    parser.add_argument('--tar_size', type=int, default=512,
                        help='size for rendering window. We use a square window.')
    parser.add_argument('--padding_ratio', type=float, default=1.0,
                        help='enlarge the face detection bbox by a margin.')
    parser.add_argument('--recon_model', type=str, default='faceverse',
                        help='choose a 3dmm model, default: faceverse')
    parser.add_argument('--first_rf_iters', type=int, default=500,
                        help='iteration number of landmark fitting for the first frame in video fitting.')
    parser.add_argument('--first_nrf_iters', type=int, default=300,
                        help='iteration number of differentiable fitting for the first frame in video fitting.')
    parser.add_argument('--rest_rf_iters', type=int, default=50,
                        help='iteration number of landmark fitting for the remaining frames in video fitting.')
    parser.add_argument('--rest_nrf_iters', type=int, default=30,
                        help='iteration number of differentiable fitting for the remaining frames in video fitting.')
    parser.add_argument('--rf_lr', type=float, default=1e-2,
                        help='learning rate for landmark fitting')
    parser.add_argument('--nrf_lr', type=float, default=1e-2,
                        help='learning rate for differentiable fitting')
    parser.add_argument('--lm_loss_w', type=float, default=3e3,
                        help='weight for landmark loss')
    parser.add_argument('--rgb_loss_w', type=float, default=1.6,
                        help='weight for rgb loss')
    parser.add_argument('--id_reg_w', type=float, default=1e-3,
                        help='weight for id coefficient regularizer')
    parser.add_argument('--exp_reg_w', type=float, default=1.5e-4,
                        help='weight for expression coefficient regularizer')
    parser.add_argument('--tex_reg_w', type=float, default=3e-4,
                        help='weight for texture coefficient regularizer')
    parser.add_argument('--tex_w', type=float, default=1,
                        help='weight for texture reflectance loss.')

    args = parser.parse_args()

    device = 'cuda'
    # args.input = "/opt/data/dongyang/data/blendshape_0826/data_train_lip/20220703_MySlate_12/MySlate_12_柯先生的iPhone.mov"
    # args.res_folder = "blendshape_0915/20220703_MySlate_12"
    # tracking(args, device)
    import glob

    train_paths = '/opt/data/dongyang/data/blendshape_0826/'
    # val_paths = '/opt/data/dongyang/data/blendshape_0826/data_eval/*'

    # for train_name in ["data_train_blink_mouth", "data_train_calibrated", "data_train_eyeclose", "data_train_lip", "data_train_no_calib", "data_train_public"]:
    for train_name in ["data_train_calibrated", "data_train_eyeclose", "data_train_lip", "data_train_no_calib", "data_train_public"]:
        train_folder = os.path.join(train_paths, train_name)
        for subname in os.listdir(train_folder):
            subtrain_folder = os.path.join(train_folder, subname)
            try:
                args.input = glob.glob(os.path.join(subtrain_folder, "*.mov"))[0]
                args.res_folder = os.path.join("blendshape_0915", train_name, subname)
                tracking(args, device)
            except:
                print(subtrain_folder)


