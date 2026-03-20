(function () {
    function clamp(value, minimum, maximum) {
        return Math.min(Math.max(value, minimum), maximum);
    }

    function initAvatarUploader(root) {
        var fileInput = root.querySelector('[data-avatar-file-input]');
        var previewPanel = root.querySelector('[data-avatar-preview-panel]');
        var originalPreview = root.querySelector('[data-avatar-original-preview]');
        var cropPreview = root.querySelector('[data-avatar-crop-preview]');
        var cropToggle = root.querySelector('[data-avatar-crop-toggle]');
        var cropControls = root.querySelector('[data-avatar-crop-controls]');
        var zoomRange = root.querySelector('[data-avatar-zoom-range]');
        var centerXRange = root.querySelector('[data-avatar-center-x-range]');
        var centerYRange = root.querySelector('[data-avatar-center-y-range]');
        var errorBox = root.querySelector('[data-avatar-error]');
        var submitButton = root.querySelector('[data-avatar-submit]');
        var hiddenCropMode = root.querySelector('[data-avatar-crop-mode]');
        var hiddenCropZoom = root.querySelector('[data-avatar-crop-zoom]');
        var hiddenCropCenterX = root.querySelector('[data-avatar-crop-center-x]');
        var hiddenCropCenterY = root.querySelector('[data-avatar-crop-center-y]');
        var maxUploadBytes = parseInt(root.getAttribute('data-max-upload-bytes') || '0', 10);
        var currentImage = null;

        if (!fileInput) {
            return;
        }

        function setError(message) {
            if (!errorBox) {
                return;
            }
            if (message) {
                errorBox.textContent = message;
                errorBox.classList.remove('d-none');
                if (submitButton) {
                    submitButton.disabled = true;
                }
            } else {
                errorBox.textContent = '';
                errorBox.classList.add('d-none');
                if (submitButton) {
                    submitButton.disabled = false;
                }
            }
        }

        function resetHiddenCropFields() {
            if (hiddenCropMode) hiddenCropMode.value = '';
            if (hiddenCropZoom) hiddenCropZoom.value = '1';
            if (hiddenCropCenterX) hiddenCropCenterX.value = '0.5';
            if (hiddenCropCenterY) hiddenCropCenterY.value = '0.5';
        }

        function renderPreview() {
            if (!currentImage) {
                return;
            }

            var cropEnabled = Boolean(cropToggle && cropToggle.checked);
            if (cropControls) {
                cropControls.classList.toggle('d-none', !cropEnabled);
            }

            if (!cropEnabled) {
                resetHiddenCropFields();
                if (originalPreview) {
                    originalPreview.src = currentImage.src;
                    originalPreview.classList.remove('d-none');
                }
                if (cropPreview) {
                    cropPreview.classList.add('d-none');
                }
                return;
            }

            var zoom = clamp(parseFloat((zoomRange && zoomRange.value) || '1'), 1, 4);
            var centerX = clamp(parseFloat((centerXRange && centerXRange.value) || '0.5'), 0, 1);
            var centerY = clamp(parseFloat((centerYRange && centerYRange.value) || '0.5'), 0, 1);
            var naturalWidth = currentImage.naturalWidth || currentImage.width;
            var naturalHeight = currentImage.naturalHeight || currentImage.height;
            var cropSide = Math.max(1, Math.floor(Math.min(naturalWidth, naturalHeight) / zoom));
            var cropLeft = Math.round((centerX * naturalWidth) - (cropSide / 2));
            var cropTop = Math.round((centerY * naturalHeight) - (cropSide / 2));
            cropLeft = clamp(cropLeft, 0, naturalWidth - cropSide);
            cropTop = clamp(cropTop, 0, naturalHeight - cropSide);

            if (hiddenCropMode) hiddenCropMode.value = 'square';
            if (hiddenCropZoom) hiddenCropZoom.value = String(zoom);
            if (hiddenCropCenterX) hiddenCropCenterX.value = String(centerX);
            if (hiddenCropCenterY) hiddenCropCenterY.value = String(centerY);

            if (originalPreview) {
                originalPreview.classList.add('d-none');
            }
            if (!cropPreview) {
                return;
            }

            var canvasSize = 320;
            cropPreview.width = canvasSize;
            cropPreview.height = canvasSize;
            var context = cropPreview.getContext('2d');
            context.clearRect(0, 0, canvasSize, canvasSize);
            context.drawImage(currentImage, cropLeft, cropTop, cropSide, cropSide, 0, 0, canvasSize, canvasSize);
            cropPreview.classList.remove('d-none');
        }

        function handleLoadedImage(imageUrl) {
            var image = new window.Image();
            image.onload = function () {
                currentImage = image;
                if (previewPanel) {
                    previewPanel.classList.remove('d-none');
                }
                renderPreview();
            };
            image.onerror = function () {
                currentImage = null;
                setError('The selected file could not be previewed. Please choose a valid image.');
            };
            image.src = imageUrl;
        }

        fileInput.addEventListener('change', function (event) {
            setError('');
            resetHiddenCropFields();
            var file = event.target.files && event.target.files[0];
            if (!file) {
                currentImage = null;
                if (previewPanel) {
                    previewPanel.classList.add('d-none');
                }
                return;
            }

            if (maxUploadBytes && file.size > maxUploadBytes) {
                currentImage = null;
                if (previewPanel) {
                    previewPanel.classList.add('d-none');
                }
                setError('This file is too large to upload. Please choose a smaller image.');
                return;
            }

            var reader = new FileReader();
            reader.onload = function (loadEvent) {
                handleLoadedImage(loadEvent.target.result);
            };
            reader.onerror = function () {
                currentImage = null;
                setError('The selected file could not be read. Please try another image.');
            };
            reader.readAsDataURL(file);
        });

        [cropToggle, zoomRange, centerXRange, centerYRange].forEach(function (control) {
            if (!control) {
                return;
            }
            control.addEventListener('input', renderPreview);
            control.addEventListener('change', renderPreview);
        });
    }

    document.addEventListener('DOMContentLoaded', function () {
        document.querySelectorAll('[data-avatar-upload-root]').forEach(initAvatarUploader);
    });
})();
