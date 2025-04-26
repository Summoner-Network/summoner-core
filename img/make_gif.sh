ffmpeg -y -i img/merchants_demo.mov -vf "fps=25,scale=800:-1:flags=lanczos,palettegen" img/palette.png

ffmpeg -i img/merchants_demo.mov -i img/palette.png -filter_complex "fps=25,scale=800:-1:flags=lanczos[x];[x][1:v]paletteuse" img/merchants_demo.gif

