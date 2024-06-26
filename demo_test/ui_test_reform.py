from concurrent.futures.thread import _worker
import cv2
import time
import numpy as np
import threading
from collections import namedtuple
from pathlib import Path

import sys
import os
import psutil
sys.path.append(os.path.dirname(os.path.abspath(os.path.dirname(__file__))))

from PyQt5.QtCore import Qt, QTimer, QObject, pyqtSignal, pyqtSlot
from PyQt5.QtGui import QImage, QPixmap
from PyQt5.QtWidgets import QApplication, QLabel, QWidget, QVBoxLayout, QPushButton, QTextEdit
from PyQt5.QtGui import QFont

from tools import mediapipe_utils as mpu
from tools.FPS import FPS, now
from hand_pose_estimation.hand_pose_estimation_module import HandPoseEstimation
from hand_pose_estimation.hand_tracker_module import HandTracker
from hand_pattern_recognition.hand_pattern_recognition_module import HandPatternRecognition
from ai_modeling.image_inferencing_module import ImageInferencing
from image_preprocessing.optimization_preprocessing_module import optimization_preprocessing
from expression_calculating.calculator_module import Calculator

class App(QWidget):
    def __init__(self, w, h):
        super().__init__()
        self.title = 'Hand Draw Formula'
        self.left, self.top = 10, 50
        self.width, self.height = w, h
        self.init_UI() 

        self.cap = cv2.VideoCapture(0)

        # define x,y,z for saving key point
        self.points = 21
        self.hprx = []
        self.hpry = []
        self.hprz = []
        for i in range(0, self.points, 1):
            self.hprx.append(0)
            self.hpry.append(0)
            self.hprz.append(0)

        # define 8_x,y,z for making image
        self.x_8, self.y_8 = [], []
        self.each_line_contain_points = []

        # define flags and string_buf
        self.each_line_contain_points_flag = False
        self.execute_flag = False
        self.list_flag = False
        self.string_buf = []
        self.start_time = 0

        # define save_frames
        self.save_frames = 0

        # call HandPoseEstimation
        model_path = 'ai_modeling/model'
        #self.hpe = HandPoseEstimation(model_path)
        self.ht = HandTracker( input_src='0',
                               pd_device='CPU',
                               pd_score_thresh=0.5, pd_nms_thresh=0.3,
                               use_lm=True,
                               lm_device='CPU',
                               lm_score_threshold=0.3,
                               use_gesture=False,
                               crop=True)
 
        self.fps = FPS(mean_nb_frames=30)
        
        self.nb_pd_inferences, self.nb_lm_inferences = 0, 0
        self.glob_pd_rtrip_time, self.glob_lm_rtrip_time = 0, 0

        # call HandPatternRecognition class
        self.hpr = HandPatternRecognition(self.hprx, self.hpry, self.hprz, 9)

        # call Calculator class
        self.calc = Calculator()
        
        # call preprocessing class
        self.desired_width = 45
        self.desired_height = 45
        self.preprocessing = optimization_preprocessing(self.desired_width, self.desired_height)

        # call and set inferencing module
        input_shape = np.zeros((self.desired_width, self.desired_height, 3))
        self.infer = ImageInferencing(model_path, 'CPU', input_shape)

        # str save list
        self.str_buf = ""
        self.str = ""
        self.str2 = f'counter: {self.preprocessing.result_counter}'
    
    def init_UI(self):
        self.ratio_w, self.ratio_h = self.width / 1920, self.height / 1000
        
        #font size
        self.font_size = int(50 * self.ratio_w)
        
        #label(1~6)
        self.label1 = QLabel(self)
        self.label1.move(10, 10)
        self.label1.resize(int(1280 * self.ratio_w), int(960 * self.ratio_h))
        self.label2 = QLabel(self)
        self.label2.move(int(1350 * self.ratio_w), int(20 * self.ratio_h))
        self.label2.resize(int(450 * self.ratio_w), int(450 * self.ratio_h))
        self.label3 = QLabel('Current formula  ', self)
        self.label3.move(int(1350 * self.ratio_w), int(500 * self.ratio_h))
        self.label4 = QLabel('None                     ', self)
        self.label4.move(int(1350 * self.ratio_w), int(550 * self.ratio_h))
        self.label5 = QLabel('Result : ', self)
        self.label5.move(int(1350 * self.ratio_w), int(800 * self.ratio_h))
        self.label6 = QLabel('INVAILD                  ', self)
        self.label6.move(int(1350 * self.ratio_w), int(850 * self.ratio_h))

        # font setting
        font = QFont('Arial', self.font_size)
        for label in [self.label1, self.label2, self.label3, self.label4, self.label5, self.label6]:
            label.setFont(font)

        # start timer for UI
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.update_images)
        self.timer.start(5)
 
        self.setWindowTitle(self.title)
        self.setGeometry(self.left, self.top, self.width, self.height)
        self.show()
            
    def update_images(self):
        # to calculate running time, save start time to start_time
        start_time = time.time()
        
        self.fps.update()

        pid = os.getpid()
        proc = psutil.Process(pid)
        cpu_usage = proc.cpu_percent()
        mem_usage = round(proc.memory_info()[0] / 2. ** 30, 2)

        # read the frame from webcam
        ret, frame = self.cap.read()
        if not ret:
            print("[GUI] Could Not read frame...")
            self.timer.stop()
            return
        
        frame_nn = frame.copy()

        self.height, self.width = frame.shape[:2]
        self.ht.frame_size = min(self.height, self.width)
        dx = (self.width - self.ht.frame_size) // 2
        dy = (self.height - self.ht.frame_size) // 2
        resized_frame = frame[dy:dy+self.ht.frame_size, dx:dx+self.ht.frame_size]

        # Resize image to NN square input shape
        frame_nn = cv2.resize(resized_frame, (self.ht.pd_w, self.ht.pd_h), interpolation=cv2.INTER_AREA)
        
        # Transpose hxwx3 -> 1x3xhxw
        frame_nn = np.transpose(frame_nn, (2,0,1))[None,]
        annotated_frame = resized_frame.copy()

        # Get palm detection
        self.pd_rtrip_time = now()
        inference = self.ht.pd_exec_net([frame_nn])
        self.glob_pd_rtrip_time += now() - self.pd_rtrip_time
        self.ht.pd_postprocess(inference)
        self.ht.pd_render(annotated_frame)
        self.nb_pd_inferences += 1
     
        self.handedness = 0

        # Hand landmarks
        for i, r in enumerate(self.ht.regions):
            frame_nn = mpu.warp_rect_img(r.rect_points, resized_frame, self.ht.lm_w, self.ht.lm_h)
            # Transpose hxwx3 -> 1x3xhxw
            frame_nn = np.transpose(frame_nn, (2,0,1))[None,]
            # Get hand landmarks
            lm_rtrip_time = now()
            inference = self.ht.lm_exec_net([frame_nn])
            self.glob_lm_rtrip_time += now() - lm_rtrip_time
            self.nb_lm_inferences += 1
            self.ht.lm_postprocess(r, inference)
            self.ht.lm_render(annotated_frame, r)
            if r.lm_score > self.ht.lm_score_threshold:
                self.handedness += 1

        # if detect hand, start HandPatternRecognition
        if self.handedness > 0:
            # Print some stats
            print(f"# palm detection inferences : {self.nb_pd_inferences}")
            print(f"# hand landmark inferences  : {self.nb_lm_inferences}")
            print(f"Palm detection round trip   : {self.glob_pd_rtrip_time/self.nb_pd_inferences*1000:.1f} ms")
            print(f"Hand landmark round trip    : {self.glob_lm_rtrip_time/self.nb_lm_inferences*1000:.1f} ms")    
            
            # save point to HandPatternRecognition
            for i, r in enumerate(self.ht.regions):
                src = np.array([(0, 0), (1, 0), (1, 1)], dtype=np.float32)
                dst = np.array([ (x, y) for x,y in r.rect_points[1:]], dtype=np.float32)
                mat = cv2.getAffineTransform(src, dst)
                lm_xy = np.expand_dims(np.array([(l[0], l[1]) for l in r.landmarks]), axis=0)
                lm_xy = np.squeeze(cv2.transform(lm_xy, mat)).astype(np.int32)
                #lm_xyz = np.squeeze(cv2.transform(lm_xyz, mat)).astype(np.float32)
                for j in range(self.points):
                    self.hprx[j], self.hpry[j] = (lm_xy[j][0] + 1) / self.width, (lm_xy[j][1] + 1) / self.height
             
            self.hpr.set_3d_position(self.hprx, self.hpry, self.hprz)

            # pick out mode pattern for avoiding scattering
            mode_pattern = self.hpr.check_switch_pattern()

            # status[0:stop, 1:write, 2:enter, 3:erase]
            if mode_pattern == 0:
                self.execute_flag = True
                if self.str != "Stop mode":
                    self.str = "Stop mode"
                
                # save each_line_contain_points
                if self.each_line_contain_points_flag == True:
                    self.each_line_contain_points_flag = False
                    self.each_line_contain_points.append(len(self.x_8))

            # execute each status
            # writing action
            if self.execute_flag is True and mode_pattern == 1:
                # saving points save in x, y
                self.x_8.append(self.hprx[8] * self.width)
                self.y_8.append(self.hpry[8] * self.height)
                self.save_frames += 1
                if self.str != "writing mode":
                    self.str = "writing mode"
                
                # flags up
                self.each_line_contain_points_flag = True
                self.execute_flag = True
                self.list_flag = True

            # enter action
            elif self.execute_flag is True and mode_pattern == 2:
                if self.list_flag is True:
                    self.each_line_contain_points.append(len(self.x_8))

                    # list to image
                    self.preprocessing.create_image_from_point(self.x_8, self.y_8, self.each_line_contain_points, 1)
                    
                    # initialize value for next image
                    self.save_frames = 0
                    self.x_8 = []
                    self.y_8 = []
                    self.each_line_contain_points = []

                    if self.str != "enter mode":
                        self.str = "enter mode"
                    try:
                        self.str2 = f'counter: {self.preprocessing.result_counter}'
                    except Exception:
                        print("Error : fail to access preprocess")

                    # inferencing and make string
                    string_image = self.preprocessing.result_image[self.preprocessing.result_counter - 1]
                    self.string_buf.append(f'{self.infer.get_inferencing_result(string_image, False)}')
                    self.str_buf = "".join(self.string_buf)
                    print(self.str_buf)
                    print(self.calc.eval_proc(self.str_buf))

                    # flag down
                    self.each_line_contain_points_flag = False
                    self.execute_flag = False
                    self.list_flag = False

                else:
                    print("List is empty. Please draw number or sign")
                    self.execute_flag = False

            # erase action
            elif self.execute_flag is True and mode_pattern == 3:
                if self.list_flag is True:
                    # erase list
                    self.x_8 = []
                    self.y_8 = []
                    self.each_line_contain_points = []
                    self.save_frames = 0
                    if self.str != "erase list":
                        self.str = "erase list"
                    
                    # flags down
                    self.each_line_contain_points_flag = False
                    self.execute_flag = False
                    self.list_flag = False

                else:
                    # erase picture or move index
                    if self.preprocessing.result_counter != 0:
                        self.preprocessing.result_counter -= 1
                        self.preprocessing.result_image[self.preprocessing.result_counter] = np.ones((self.desired_width, self.desired_height, 3), dtype=np.uint8)*255
                        del self.string_buf[self.preprocessing.result_counter]
                        self.str_buf = "".join(self.string_buf)
                        print(self.str_buf)
                        print(self.calc.eval_proc(self.str_buf))

                    if self.str != "erase picture":
                        self.str = "erase picture"
                    self.str2 = f'counter: {self.preprocessing.result_counter}'

                    self.execute_flag = False

        # Load images from file or camera
        annotated_frame = cv2.resize(annotated_frame,(int(1280 * self.ratio_w), int(960 * self.ratio_h)))
        self.fps.display(annotated_frame, orig=(int((1280 * 0.72) * self.ratio_w), 50), color=(240,180,100))
        
        # show detection_result to visible
        cv2.putText(annotated_frame, self.str, (5, 50), cv2.FONT_HERSHEY_SIMPLEX, 2, (0, 0, 0), 5)
        cv2.putText(annotated_frame, self.str2, (5, 100), cv2.FONT_HERSHEY_SIMPLEX, 2, (0, 0, 0), 2) 
        cv2.putText(annotated_frame, f"CPU: {cpu_usage:.2f}%", (int((1280 * 0.714) * self.ratio_w), 100), cv2.FONT_HERSHEY_SIMPLEX, 2, (240, 180, 100), 2)
        cv2.putText(annotated_frame, f"MEM: {mem_usage:.2f}%", (int((1280 * 0.709) * self.ratio_w), 150), cv2.FONT_HERSHEY_SIMPLEX, 2, (240, 180, 100), 2)

        h, w, c = annotated_frame.shape
        img1 = QImage(annotated_frame.data, w, h, w * c, QImage.Format_BGR888)
        pixmap1 = QPixmap.fromImage(img1)
        
        if self.preprocessing.result_counter == 0:
            img3 = cv2.imread('./demo_test/intel_logo.png')
            convert_img = cv2.resize(img3,(int(450 * self.ratio_w),int(450 * self.ratio_h)))
            convert_img = cv2.cvtColor(convert_img,cv2.COLOR_BGR2RGB)
            h, w, c = convert_img.shape
            img2 = QImage(convert_img.data, w, h, w * c, QImage.Format_RGB888)
        else:
            show_img = self.preprocessing.result_image[self.preprocessing.result_counter-1]
            show_new_img = cv2.resize(show_img,(int(450 * self.ratio_w), int(450 * self.ratio_h)))
            h, w, c = show_new_img.shape
            img2 = QImage(show_new_img.data, w, h, w * c, QImage.Format_RGB888)
        pixmap2 = QPixmap.fromImage(img2)

        # Update labels with new images
        self.label1.setPixmap(pixmap1)
        self.label2.setPixmap(pixmap2)

        # Update label with new text
        self.label4.setText(self.str_buf)
        if self.calc.eval_proc(self.str_buf) == 'INVALID':
            self.label6.setText(self.calc.eval_proc(self.str_buf))
        else:
            self.label6.setText(str(self.calc.eval_proc(self.str_buf)))
    
    def keyPressEvent(self, e):
        if e.key() == Qt.Key_Escape:
            print("Esc pressed. PyQt owari...")
            self.close()
        elif e.key() == Qt.Key_Q:
            print("q pressed. PyQt owari...")
            self.close()

if __name__ == "__main__":
    main = QApplication(sys.argv)
    main_rect = main.desktop().screenGeometry()
    width, height = main_rect.width(), main_rect.height()
    ex = App(width, height)
    sys.exit(main.exec_())

