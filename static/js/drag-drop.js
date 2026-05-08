class DragDropUpload {
    constructor(zoneId) {
        this.zone = document.getElementById(zoneId);
        if (!this.zone) {
            throw new Error(`Drop zone ${zoneId} not found`);
        }
        this.maxSize = 20 * 1024 * 1024; // 20MB
        this.allowedTypes = ['application/pdf'];
        this.setupListeners();
    }

    setupListeners() {
        this.zone.addEventListener('dragover', (e) => {
            e.preventDefault();
            this.zone.classList.add('drag-over');
        });

        this.zone.addEventListener('dragleave', () => {
            this.zone.classList.remove('drag-over');
        });

        this.zone.addEventListener('drop', (e) => {
            e.preventDefault();
            this.zone.classList.remove('drag-over');
            const files = e.dataTransfer.files;
            if (files.length > 0) {
                this.handleFile(files[0]);
            }
        });

        // Click to upload
        this.zone.addEventListener('click', () => {
            const input = document.createElement('input');
            input.type = 'file';
            input.accept = '.pdf';
            input.onchange = (e) => {
                if (e.target.files.length > 0) {
                    this.handleFile(e.target.files[0]);
                }
            };
            input.click();
        });
    }

    isValidFile(file) {
        if (!this.allowedTypes.includes(file.type)) return false;
        if (file.size > this.maxSize) return false;
        return true;
    }

    handleFile(file) {
        if (!this.isValidFile(file)) {
            this.showError('Invalid file. Please upload a PDF under 20MB.');
            return;
        }
        this.uploadFile(file);
    }

    showError(message) {
        const toast = document.createElement('div');
        toast.className = 'toast error';
        toast.textContent = message;
        document.body.appendChild(toast);
        setTimeout(() => toast.remove(), 3000);
    }

    uploadFile(file) {
        const formData = new FormData();
        formData.append('file', file);
        formData.append('auto_cut', 'true');

        fetch('/upload', { method: 'POST', body: formData })
            .then(response => response.json())
            .then(data => {
                if (data.error) {
                    this.showError(data.error);
                } else {
                    this.onUploadSuccess(data);
                }
            })
            .catch(err => this.showError('Upload failed'));
    }

    onUploadSuccess(data) {
        // To be overridden by integration code
        console.log('Upload successful', data);
    }
}

window.DragDropUpload = DragDropUpload;
