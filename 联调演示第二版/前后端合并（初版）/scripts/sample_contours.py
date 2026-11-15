"""仅用于自制几何示例：按已知绘图颜色提取可见轮廓，不用于任意上传图片。"""
import cv2
import numpy as np

palettes={
'星星':[(255,240,170)],'月亮':[(255,244,190),(240,226,160)],
'大树':[(30,96,66),(110,78,50)],
'朵朵':[(250,250,250),(255,190,200),(60,130,220),(40,40,60)],
'围巾':[(60,130,220)],'乌龟爷爷':[(120,180,90),(40,110,70),(24,80,50),(30,30,30)],
'小河':[(40,96,170),(120,170,230),(236,228,170)],'月亮的影子':[(236,228,170)],
'芦苇':[(96,150,70),(132,96,60)],'山坡':[(50,128,88)],
'小房子':[(196,150,104),(150,70,60),(255,220,110),(150,110,60)],
'窗户':[(255,220,110),(150,110,60)]}

def add_contours(image, objects):
    a = np.array(image.convert('RGB'))
    for obj in objects:
        mask = np.zeros(a.shape[:2], np.uint8)
        for color in palettes.get(obj['name'], []):
            mask[np.all(a == color, axis=2)] = 255
        x1,y1,x2,y2 = obj['bbox']
        crop = np.zeros_like(mask)
        crop[y1:y2+1,x1:x2+1] = mask[y1:y2+1,x1:x2+1]
        contours,_ = cv2.findContours(crop,cv2.RETR_EXTERNAL,cv2.CHAIN_APPROX_SIMPLE)
        obj['contours'] = [cv2.approxPolyDP(c,1.2,True).reshape(-1,2).tolist() for c in contours if cv2.contourArea(c)>6]
